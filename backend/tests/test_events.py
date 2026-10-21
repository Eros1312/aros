"""Offline contracts for private originals and Canva's owner-scoped OAuth/jobs."""
import asyncio
import base64
import hashlib
import io
import json
from urllib.parse import parse_qs, urlsplit

import pymupdf
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import events
from app.config import settings
from app.connectors import encode_tokens, decode_tokens
from app.database import Base, User, Item, Connector, OAuthState
from app.security import expiry

@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, 'encryption_key', Fernet.generate_key().decode())
    monkeypatch.setattr(settings, 'openclaw_url', 'https://gateway.fixture')
    monkeypatch.setattr(settings, 'openclaw_token', 'fixture-private-gateway')
    monkeypatch.setattr(events, 'SessionLocal', sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(events.httpx, 'Client', lambda *args, **kwargs: pytest.fail('External network prohibited'))
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()

@pytest.fixture
def users(db):
    owner = User(email='events-owner@fixture', password_hash='fixture', ai_consent=True)
    other = User(email='events-other@fixture', password_hash='fixture', ai_consent=True)
    db.add_all([owner, other]); db.commit()
    return owner, other

def configured(db, owner):
    return events.configure(events.ApplicationIn(client_id='fixture-client', client_secret='fixture-app-secret'), owner, db)

def connected(db, owner, expired=False):
    configured(db, owner)
    row = Connector(id='canva', user_id=owner.id, name='Canva', status='connected', revision=3,
                    encrypted_tokens=encode_tokens({'access_token':'fixture-access', 'refresh_token':'fixture-refresh',
                                                   'expires_at':'2000-01-01T00:00:00+00:00' if expired else expiry(60)}))
    db.add(row); db.commit()
    return row

def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page(); page.insert_text((50, 50), 'Eventos original fixture')
        return doc.tobytes()

def import_original(db, owner, raw=None):
    return asyncio.run(events.import_file(owner, db, file=UploadFile(filename='cartel.pdf', file=io.BytesIO(raw or pdf_bytes())),
                    category='poster', provenance='Authorized test', review_state='original', canva_edit_url='', canva_view_url=''))

def test_original_dedup_and_owner_isolation(db, users):
    owner, other = users; raw = pdf_bytes()
    first = import_original(db, owner, raw); repeated = import_original(db, owner, raw)
    separate = import_original(db, other, raw)
    assert first['id'] == repeated['id'] != separate['id']
    assert len(events.library(owner, db)['assets']) == 1
    assert events.library(other, db)['assets'][0]['id'] == separate['id']
    assert db.get(Item, first['id']).blob == raw
    assert first['data']['sha256'] == hashlib.sha256(raw).hexdigest()

def test_preview_private_and_other_owner_hidden(db, users):
    owner, other = users; asset = import_original(db, owner)
    response = events.preview(asset['id'], owner, db)
    assert response.media_type == 'image/jpeg' and response.body.startswith(b'\xff\xd8')
    assert response.headers['cache-control'] == 'private, no-store'
    for actor, page in [(other, 0), (owner, -1), (owner, 31)]:
        with pytest.raises(HTTPException) as exc: events.preview(asset['id'], actor, db, page)
        assert exc.value.status_code == 404

def test_context_is_owner_scoped_dedup_and_not_image(db, users):
    owner, other = users
    payload = events.ContextIn(thread_id='reference-thread', title='Eventos', text='Reference only', partial=True)
    first = events.import_context(payload, owner, db); second = events.import_context(payload, owner, db)
    assert first['id'] == second['id'] and first['data']['category'] == 'context'
    assert events.library(other, db)['assets'] == []
    with pytest.raises(HTTPException) as exc: events.preview(first['id'], owner, db)
    assert exc.value.status_code == 404

def test_application_credentials_encrypted_and_missing_oauth_is_honest(db, users):
    owner, _ = users
    with pytest.raises(HTTPException) as exc: events.authorize(owner, db)
    assert exc.value.status_code == 409
    status = configured(db, owner); row = db.get(Connector, (events.APP, owner.id))
    assert status['configured'] and not status['connected']
    assert 'fixture-app-secret' not in row.encrypted_tokens and 'fixture-app-secret' not in json.dumps(status)
    assert decode_tokens(row.encrypted_tokens)['client_secret'] == 'fixture-app-secret'

def test_pkce_owner_binding_single_use_and_encrypted_verifier(db, users, monkeypatch):
    owner, other = users; configured(db, owner)
    query = parse_qs(urlsplit(events.authorize(owner, db)['url']).query)
    state = query['state'][0]; row = db.get(OAuthState, state); verifier = decode_tokens(row.verifier)['verifier']
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    assert query['code_challenge_method'] == ['S256'] and query['code_challenge'] == [challenge]
    assert verifier not in row.verifier
    with pytest.raises(HTTPException) as exc: events.callback(state, other, db, code='fixture-code')
    assert exc.value.status_code == 400 and db.get(OAuthState, state)
    calls = []
    def exchange(application, body):
        calls.append(body)
        return {'access_token':'fixture-access', 'refresh_token':'fixture-refresh', 'expires_at':expiry(60), 'scope':' '.join(events.SCOPES)}
    monkeypatch.setattr(events, 'token_request', exchange)
    response = events.callback(state, owner, db, code='fixture-code')
    assert response.status_code == 307 and calls[0]['code_verifier'] == verifier
    assert db.get(OAuthState, state) is None
    account = db.get(Connector, ('canva', owner.id))
    assert 'fixture-access' not in account.encrypted_tokens and events.canva_status(db, owner)['connected']
    with pytest.raises(HTTPException): events.callback(state, owner, db, code='fixture-code')
    assert len(calls) == 1

def test_refresh_rotation_persisted_before_request_and_not_reused(db, users, monkeypatch):
    owner, _ = users; row = connected(db, owner, expired=True); calls = []
    def exchange(application, body):
        calls.append(body)
        return {'access_token':'rotated-access', 'refresh_token':'rotated-refresh', 'expires_at':expiry(60)}
    monkeypatch.setattr(events, 'token_request', exchange)
    assert events.access_token(db, owner) == ('rotated-access', 3)
    assert decode_tokens(row.encrypted_tokens)['refresh_token'] == 'rotated-refresh'
    assert events.access_token(db, owner)[0] == 'rotated-access' and len(calls) == 1
    assert calls[0]['refresh_token'] == 'fixture-refresh'

def test_callback_without_required_scopes_does_not_connect(db, users, monkeypatch):
    owner, _ = users; configured(db, owner)
    state = parse_qs(urlsplit(events.authorize(owner, db)['url']).query)['state'][0]
    monkeypatch.setattr(events, 'token_request', lambda *args: {'access_token':'fixture-access', 'refresh_token':'fixture-refresh', 'scope':'design:meta:read'})
    with pytest.raises(HTTPException) as exc: events.callback(state, owner, db, code='fixture-code')
    assert exc.value.status_code == 400 and not events.canva_status(db, owner)['connected']
    assert db.get(OAuthState, state) is None

def test_design_model_fixed_and_reference_owned(db, users):
    owner, other = users; connected(db, owner)
    with pytest.raises(ValidationError): events.BriefIn(brief='Create a useful design', model='test/model')
    foreign = import_original(db, other)
    with pytest.raises(HTTPException) as exc: events.design(events.BriefIn(brief='Create a useful design', asset_id=foreign['id']), owner, db)
    assert exc.value.status_code == 404
    created = events.design(events.BriefIn(brief='Create a useful design'), owner, db)
    assert created['data']['model'] == events.ADVANCED_MODEL and created['data']['state'] == 'queued'
    with pytest.raises(HTTPException) as exc: events.design(events.BriefIn(brief='Another useful design'), owner, db)
    assert exc.value.status_code == 409

def test_failed_job_stops_without_fallback_or_retry(db, users, monkeypatch):
    owner, _ = users; connected(db, owner)
    created = events.design(events.BriefIn(brief='Create a useful design'), owner, db)
    calls = []
    def fail_plan(*args): calls.append(args[-1].id); raise ValueError('provider fixture failure')
    monkeypatch.setattr(events, 'generate_plan', fail_plan)
    events.process_canva_jobs(); db.expire_all(); row = db.get(Item, created['id'])
    assert row.status == 'cancelled' and row.data['state'] == 'failed'
    assert row.data['model'] == 'test/design-model' and 'provider fixture failure' not in row.data['error']
    events.process_canva_jobs(); assert calls == [created['id']]

def test_revoked_ai_consent_blocks_queued_job_without_external_call(db, users):
    owner, _ = users; connected(db, owner)
    created = events.design(events.BriefIn(brief='Create a useful design'), owner, db)
    owner.ai_consent = False; db.commit(); events.process_canva_jobs(); db.expire_all()
    row = db.get(Item, created['id']); assert row.status == 'cancelled' and row.data['state'] == 'blocked'

@pytest.mark.parametrize('url',['http://www.canva.com/design/x','https://canva.com.evil.test/design/x','https://user:pass@www.canva.com/design/x','https://www.canva.com:8443/design/x'])
def test_canva_links_reject_other_origins(url):
    with pytest.raises(HTTPException): events.safe_canva_url(url)

def test_missing_encryption_does_not_configure_or_expose_secret(db, users, monkeypatch):
    owner, _ = users
    monkeypatch.setattr(settings, 'encryption_key', '')
    with pytest.raises(HTTPException) as exc:
        configured(db, owner)
    assert exc.value.status_code == 409
    assert 'fixture-app-secret' not in str(exc.value.detail)
    assert not events.canva_status(db, owner)['configured']

def test_application_change_invalidates_issued_oauth_state(db, users):
    owner, _ = users; configured(db, owner)
    state = parse_qs(urlsplit(events.authorize(owner, db)['url']).query)['state'][0]
    events.configure(events.ApplicationIn(client_id='second-client', client_secret='second-secret'), owner, db)
    assert db.get(OAuthState, state) is None
    with pytest.raises(HTTPException) as exc: events.callback(state, owner, db, code='fixture-code')
    assert exc.value.status_code == 400
