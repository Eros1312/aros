"""Offline privacy regressions: gateway failures must fail closed."""
from datetime import datetime,timedelta,timezone
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from test_api import client,auth
from app import channels
from app.config import settings
from app.database import Connector,SessionLocal,User
from app.connectors import decode_tokens

PHONE='+34600000000'

@pytest.fixture(autouse=True)
def private_gateway(monkeypatch):
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    monkeypatch.setattr(settings,'openclaw_token','test-private-token')
    calls=[]
    def rpc(method,params):
        calls.append((method,params))
        if method=='config.get':return {'hash':'test-hash'}
        return {'connected':False,'sessionKey':'test-session'}
    monkeypatch.setattr(channels,'rpc',rpc)
    return calls

def connect(auth):
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    result=auth.post('/api/v1/openclaw/whatsapp/start',json={'phone':PHONE})
    assert result.status_code==200,result.text

def test_start_encrypts_owner_and_restricts_account_overrides(auth,private_gateway):
    import json
    connect(auth)
    patch=json.loads(next(p['raw'] for m,p in private_gateway if m=='config.patch'))['channels']['whatsapp']
    for policy in [patch,patch['accounts']['default']]:
        assert policy['dmPolicy']=='allowlist' and policy['allowFrom']==[PHONE]
        assert policy['groupPolicy']=='disabled' and policy['selfChatMode'] is True
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email==settings.admin_email))
        row=db.get(Connector,(channels.CONTROL,user.id))
        assert PHONE not in row.encrypted_tokens
        assert decode_tokens(row.encrypted_tokens)['phone']==PHONE
        assert row.sync_details=={'desired':'enabled','pending':False}

def test_revocation_retries_gateway_failure_without_reactivation(auth,monkeypatch,private_gateway):
    connect(auth)
    def unavailable(method,params):raise HTTPException(503,'Gateway unavailable')
    monkeypatch.setattr(channels,'rpc',unavailable)
    assert auth.patch('/api/v1/me',json={'ai_consent':False}).json()['ai_consent'] is False
    assert auth.post('/api/v1/openclaw/whatsapp/wait',json={}).status_code==409
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email==settings.admin_email))
        row=db.get(Connector,(channels.CONTROL,user.id))
        assert row.sync_details['pending'] and row.encrypted_tokens is None
        row.sync_details={**row.sync_details,'retry_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()};db.commit()
    calls=[]
    def recovered(method,params):
        calls.append((method,params))
        return {'hash':'test-hash'} if method=='config.get' else {}
    monkeypatch.setattr(channels,'rpc',recovered)
    from app.worker import tick
    tick()
    assert any(m=='channels.logout' for m,p in calls)
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email==settings.admin_email))
        row=db.get(Connector,(channels.CONTROL,user.id))
        assert row.sync_details=={'desired':'disabled','pending':False}
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    calls.clear();tick();assert not calls

def test_bridge_requires_verified_sender_and_active_control(auth,private_gateway):
    headers={'Authorization':'Bearer test-private-token'}
    endpoint='/api/v1/internal/openclaw/resources'
    assert auth.post(endpoint,json={'query':'tareas'},headers=headers).status_code==403
    connect(auth)
    for sender in [None,'+34611111111','owner']:
        supplied={**headers,**({'X-Aros-WhatsApp-Sender':sender} if sender else {})}
        assert auth.post(endpoint,json={'query':'tareas'},headers=supplied).status_code==403
    supplied={**headers,'X-Aros-WhatsApp-Sender':PHONE}
    assert auth.post(endpoint,json={'query':'tareas'},headers=supplied).status_code==200
    assert auth.post('/api/v1/openclaw/whatsapp/disconnect',json={}).status_code==200
    assert auth.post(endpoint,json={'query':'tareas'},headers=supplied).status_code==403

def test_private_control_cannot_be_listed_or_changed_through_connectors(auth,private_gateway):
    connect(auth)
    assert channels.CONTROL not in [c['id'] for c in auth.get('/api/v1/connectors').json()]
    path='/api/v1/connectors/'+channels.CONTROL
    assert auth.post(path+'/sync',json={}).status_code==404
    assert auth.patch(path,json={'status':'connected'}).status_code==404
    assert auth.delete(path).status_code==404
