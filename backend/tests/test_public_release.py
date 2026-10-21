"""Fresh-install boundaries: no provider required, no hidden outbound I/O."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]


def isolated_env(tmp_path):
    env = os.environ.copy()
    for key in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENCLAW_URL', 'OPENCLAW_TOKEN',
                'OPENCLAW_MODEL', 'OPENCLAW_DESIGN_MODEL', 'FREELLM_URL', 'FREELLM_TOKEN',
                'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'SERVER_AGENT_URL',
                'SERVER_AGENT_TOKEN', 'RSS_URLS'):
        env[key] = ''
    env.update(APP_ENV='development', DEMO_MODE='true', ADMIN_EMAIL='demo@aros.local',
               DATABASE_URL='sqlite:///'+str(tmp_path/'fresh.db'), PYTHONPATH=str(BACKEND),
               WEB_DIR=str(tmp_path/'no-web'))
    return env


def test_fresh_demo_reads_creates_and_summarizes_without_network(tmp_path):
    program = '''
import socket
def refuse_network(*args, **kwargs):
    raise AssertionError('The default local demo must not open a network connection')
socket.socket.connect = refuse_network
from fastapi.testclient import TestClient
from app.main import app
from app.worker import tick
with TestClient(app) as client:
    config = client.get('/api/v1/config').json()
    assert config['demo'] and not config['ai_configured'] and not config['agent_configured']
    assert config['ai_mode'] == 'local'
    assert client.post('/api/v1/auth/demo').status_code == 200
    client.headers['X-Aros-Client'] = 'web'
    preferences = client.get('/api/v1/ai/preferences').json()
    assert preferences['model'] == preferences['default_model'] == 'local'
    items = client.get('/api/v1/items').json()
    assert items and all(item['is_demo'] for item in items)
    response = client.post('/api/v1/items', json={'kind':'task','area':'personal','title':'Fresh install task'})
    assert response.status_code == 201, response.text
    response = client.post('/api/v1/chat', json={'text':'Resume mis tareas'})
    assert response.status_code == 200 and response.json()['mode'] == 'local'
    assert 'reglas locales' in response.json()['text']
    tick()
'''
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                            env=isolated_env(tmp_path), text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_configuration_never_selects_a_model_implicitly():
    settings = Settings(_env_file=None, openclaw_model='', openai_model='',
                        openai_api_key='', openclaw_url='', openclaw_token='')
    assert settings.default_model == 'local' and not settings.agent_configured
    settings.openai_api_key = 'synthetic-test-key'
    with pytest.raises(RuntimeError, match='OPENAI_MODEL'):
        settings.validate_production()
    settings.openai_api_key = ''
    settings.openclaw_url = 'http://gateway.invalid'
    with pytest.raises(RuntimeError, match='OPENCLAW_MODEL'):
        settings.validate_production()


def test_env_bootstrap_is_private_and_refuses_overwrite(tmp_path):
    script_dir = tmp_path/'scripts'
    script_dir.mkdir()
    (tmp_path/'deploy').mkdir()
    script = script_dir/'prepare_env.py'
    shutil.copyfile(BACKEND.parent/'scripts/prepare_env.py', script)
    command = [sys.executable, str(script), '--domain', 'aros.example.com', '--email', 'admin@example.com']
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    target = tmp_path/'deploy/.env'
    before = target.read_text()
    values = dict(line.split('=',1) for line in before.splitlines() if '=' in line)
    assert len(values['ADMIN_PASSWORD']) >= 30
    assert not target.stat().st_mode & 0o077
    assert not values['OPENAI_API_KEY'] and not values['OPENCLAW_URL']
    assert values['ADMIN_PASSWORD'] not in result.stdout + result.stderr
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert target.read_text() == before


def test_fresh_migration_chain_matches_models(tmp_path):
    env = isolated_env(tmp_path)
    for arguments in [('upgrade', 'head'), ('check',)]:
        result = subprocess.run([sys.executable, '-m', 'alembic', *arguments], cwd=BACKEND,
                                env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_optional_router_bootstrap_handles_empty_example_and_is_idempotent(tmp_path):
    script_dir=tmp_path/'scripts';script_dir.mkdir()
    (tmp_path/'deploy').mkdir()
    script=script_dir/'initialize-freellm-env.py'
    shutil.copyfile(BACKEND.parent/'scripts/initialize-freellm-env.py',script)
    target=tmp_path/'deploy/.env'
    target.write_text('ADMIN_EMAIL=admin@example.com\nFREELLM_ENCRYPTION_KEY=\nFREELLM_ADMIN_PASSWORD=\nFREELLM_URL=\n')
    result=subprocess.run([sys.executable,str(script)],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    first=target.read_text()
    values=dict(line.split('=',1) for line in first.splitlines() if '=' in line)
    assert len(values['FREELLM_ENCRYPTION_KEY'])==64
    assert values['FREELLM_URL']=='http://freellmapi:3001'
    assert first.count('FREELLM_ENCRYPTION_KEY=')==1
    assert values['FREELLM_ADMIN_PASSWORD'] not in result.stdout+result.stderr
    assert not target.stat().st_mode & 0o077
    assert subprocess.run([sys.executable,str(script)],capture_output=True).returncode==0
    assert target.read_text()==first
