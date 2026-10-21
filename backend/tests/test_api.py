import os
os.environ['DATABASE_URL']='sqlite://'
os.environ['DEMO_MODE']='true'
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from app import database
from app.main import app, rate_buckets
from app.database import Base, SessionLocal, User, Item
from app.config import settings
from app.security import hash_password
from app.worker import tick
from app.connectors import extract_order

@pytest.fixture
def client(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    monkeypatch.setattr(database,'engine',engine)
    import app.main as main
    monkeypatch.setattr(main,'engine',engine)
    database.SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine);rate_buckets.clear()
    with TestClient(app) as c:yield c
    Base.metadata.drop_all(engine);engine.dispose()
@pytest.fixture
def auth(client):
    assert client.post('/api/v1/auth/demo').status_code==200
    client.headers['X-Aros-Client']='web';return client

def task():return dict(kind='task',area='personal',title='Prueba persistente',description='Descripción',status='pending',priority='high',due_at=None,data={})
def token(c,action,payload):
    r=c.post('/api/v1/confirmations',json={'action':action,'payload':payload});assert r.status_code==200,r.text
    return r.json()['token']
def test_auth_and_csrf(client):
    assert client.get('/api/v1/items').status_code==401
    assert client.post('/api/v1/auth/login',json={'email':'aros@local','password':'wrong'}).status_code==401
    client.post('/api/v1/auth/demo')
    assert client.post('/api/v1/items',json=task(),headers={'Origin':'https://evil.test'}).status_code==403
    assert client.post('/api/v1/items',json=task()).status_code==403
    client.headers['X-Aros-Client']='web'
    assert client.post('/api/v1/items',json=task()).status_code==201
    client.post('/api/v1/auth/logout');assert client.get('/api/v1/me').status_code==401

def test_crud_concurrency_confirmations(auth):
    r=auth.post('/api/v1/items',json=task());assert r.status_code==201,r.text
    id=r.json()['id'];patch={**task(),'title':'Editado','version':1,'status':'done'}
    assert auth.patch('/api/v1/items/'+id,json=patch).status_code==200
    assert auth.patch('/api/v1/items/'+id,json=patch).status_code==409
    assert auth.delete('/api/v1/items/'+id).status_code==409
    wrong=token(auth,'item.delete',{'id':'wrong'})
    assert auth.delete('/api/v1/items/'+id,headers={'X-Confirmation':wrong}).status_code==409
    right=token(auth,'item.delete',{'id':id})
    assert auth.delete('/api/v1/items/'+id,headers={'X-Confirmation':right}).status_code==200
    assert auth.get('/api/v1/items/'+id).status_code==404

def test_event_confirmation_canonical_dates_and_replay(auth):
    payload={**task(),'kind':'event','title':'Evento','due_at':'2026-12-01T09:00:00.000Z'}
    assert auth.post('/api/v1/items',json=payload).status_code==409
    t=token(auth,'event.create',payload)
    r=auth.post('/api/v1/items',json=payload,headers={'X-Confirmation':t});assert r.status_code==201,r.text
    assert auth.post('/api/v1/items',json=payload,headers={'X-Confirmation':t}).status_code==409
    id=r.json()['id'];updated={**payload,'version':1,'title':'Actualizado'}
    t=token(auth,'event.update',{'id':id,**updated})
    assert auth.patch('/api/v1/items/'+id,json=updated,headers={'X-Confirmation':t}).status_code==200

def test_owner_isolation(auth):
    with SessionLocal() as db:
        other=User(email='other@local',password_hash=hash_password('different-password'));db.add(other);db.flush()
        foreign=Item(user_id=other.id,kind='note',area='personal',title='Secret');db.add(foreign);db.commit();id=foreign.id
    assert auth.get('/api/v1/items/'+id).status_code==404
    assert auth.delete('/api/v1/items/'+id).status_code==404
    assert all(i['id']!=id for i in auth.get('/api/v1/items').json())

def test_shared_server_mutations_require_admin_even_with_confirmation(auth,monkeypatch):
    import app.main as main
    calls=[]
    monkeypatch.setattr(main,'server_action',lambda *args:calls.append(args) or {'ok':True})
    payload={'action':'service.restart','service':'example'}
    # The administrator still needs a specific, one-use confirmation.
    assert auth.post('/api/v1/server/actions',json=payload).status_code==409
    confirmation=token(auth,'service.restart',payload)
    assert auth.post('/api/v1/server/actions',json=payload,headers={'X-Confirmation':confirmation}).status_code==200
    assert calls==[('service.restart','example')]
    with SessionLocal() as db:
        db.add(User(email='member@example.com',password_hash=hash_password('synthetic-member-password')));db.commit()
    auth.post('/api/v1/auth/logout')
    assert auth.post('/api/v1/auth/login',json={'email':'member@example.com','password':'synthetic-member-password'}).status_code==200
    for action_name in ['service.restart','backup.run']:
        payload={'action':action_name,'service':'example'}
        confirmation=token(auth,action_name,payload)
        result=auth.post('/api/v1/server/actions',json=payload,headers={'X-Confirmation':confirmation})
        assert result.status_code==403
    assert calls==[('service.restart','example')]

def test_upload_download_search_and_validation(auth):
    r=auth.post('/api/v1/documents/upload',data={'area':'learning'},files={'file':('gestión.txt','Guion de gestión y análisis'.encode(),'text/plain')});assert r.status_code==201,r.text
    id=r.json()['id'];assert auth.get('/api/v1/documents/'+id+'/download').content=='Guion de gestión y análisis'.encode()
    assert any(i['id']==id for i in auth.get('/api/v1/items?q=gestion').json())
    assert auth.post('/api/v1/documents/upload',files={'file':('bad.exe',b'data')}).status_code==422
    assert auth.post('/api/v1/documents/upload',files={'file':('bad.pdf',b'not pdf')}).status_code==422
    assert auth.post('/api/v1/items',json={**task(),'title':'   '}).status_code==422
    assert auth.post('/api/v1/items',json={**task(),'data':{'url':'javascript:alert(1)'}}).status_code==422
    assert auth.post('/api/v1/items',json={**task(),'due_at':'2026-12-01T09:00:00'}).status_code==422

def test_inbox_chat_export(auth):
    id=auth.get('/api/v1/inbox').json()[0]['id']
    assert auth.patch('/api/v1/inbox/'+id,json={'state':'snoozed'}).status_code==422
    until=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
    assert auth.patch('/api/v1/inbox/'+id,json={'state':'snoozed','snoozed_until':until}).status_code==200
    assert id not in [i['id'] for i in auth.get('/api/v1/dashboard').json()['attention']]
    r=auth.post('/api/v1/chat',json={'text':'¿Qué me falta de Eventos?'}).json()
    assert r['mode']=='local' and r['sources'] and all(s['area']=='events' for s in r['sources'])
    assert len(auth.get('/api/v1/chat').json())==2
    exported=auth.get('/api/v1/export');assert exported.status_code==200
    assert 'password_hash' not in exported.text and 'encrypted_tokens' not in exported.text
    t=token(auth,'chat.clear',{});assert auth.delete('/api/v1/chat',headers={'X-Confirmation':t}).status_code==200
    assert auth.get('/api/v1/chat').json()==[]

def test_worker_once(auth):
    a=auth.get('/api/v1/automations').json()[0]
    id=auth.post('/api/v1/automations/'+a['id']+'/run').json()['id']
    assert auth.post('/api/v1/automations/'+a['id']+'/run').json()['id']==id
    tick();tick();r=auth.get('/api/v1/automations/runs/history').json()
    assert len(r)==1 and r[0]['status']=='completed'

def test_connectors_honest_states(auth):
    for key in ['gmail','drive','notion']:assert auth.post('/api/v1/connectors/'+key+'/sync').status_code==409
    assert auth.get('/api/v1/server').json()['scope']=='Equipo que ejecuta el backend'
    assert 'encrypted_tokens' not in auth.get('/api/v1/connectors').text
    assert extract_order('Pedido Amazon enviado')==('Amazon','in_transit')
    assert auth.get('/api/v1/oauth/google/callback?state=wrong&code=example').status_code==400

def test_external_ai_requires_consent(auth,monkeypatch):
    monkeypatch.setattr(settings,'openai_api_key','fake-key-not-sent')
    assert auth.post('/api/v1/chat',json={'text':'Resumen'}).status_code==409

def test_production_disables_demo(client,monkeypatch):
    monkeypatch.setattr(settings,'app_env','production');assert client.post('/api/v1/auth/demo').status_code==403

def test_review_preserves_resolved_and_snoozed_inbox(auth):
    created=auth.post('/api/v1/items',json={**task(),'due_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}).json()
    alert=next(i for i in auth.get('/api/v1/inbox').json() if i['item_id']==created['id'])
    auth.patch('/api/v1/inbox/'+alert['id'],json={'state':'resolved'})
    review=auth.post('/api/v1/automations',json={'title':'Review','kind':'deadline_review','enabled':False,'interval_minutes':30}).json()
    auth.post('/api/v1/automations/'+review['id']+'/run');tick()
    assert next(i for i in auth.get('/api/v1/inbox').json() if i['id']==alert['id'])['state']=='resolved'
    until=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
    auth.patch('/api/v1/inbox/'+alert['id'],json={'state':'snoozed','snoozed_until':until})
    auth.post('/api/v1/automations/'+review['id']+'/run');tick()
    assert next(i for i in auth.get('/api/v1/inbox').json() if i['id']==alert['id'])['state']=='snoozed'

def test_oauth_pkce_encryption_and_one_time_state(auth,monkeypatch):
    from urllib.parse import urlparse,parse_qs
    from cryptography.fernet import Fernet
    from app.connectors import oauth_callback,decode_tokens
    from app.database import OAuthState,Connector
    import app.connectors as connectors
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    monkeypatch.setattr(settings,'google_client_id','test-client')
    monkeypatch.setattr(settings,'google_client_secret','test-secret')
    url=auth.post('/api/v1/connectors/gmail/authorize').json()['url']
    params=parse_qs(urlparse(url).query);assert params['code_challenge_method']==['S256']
    state=params['state'][0]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'access_token':'private-access','refresh_token':'private-refresh','scope':params['scope'][0],'expires_in':3600}
    class Provider:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,data):
            assert data['code_verifier'] and data['code']=='test-code'
            return Response()
    monkeypatch.setattr(connectors.httpx,'Client',Provider)
    with SessionLocal() as db:
        uid=db.get(OAuthState,state).user_id
        assert oauth_callback(db,state,'test-code')
        connector=db.get(Connector,('gmail',uid))
        assert 'private-access' not in connector.encrypted_tokens
        assert decode_tokens(connector.encrypted_tokens)['refresh_token']=='private-refresh'
        with pytest.raises(Exception) as exc:oauth_callback(db,state,'test-code')
        assert exc.value.status_code==400
    assert 'private-access' not in auth.get('/api/v1/connectors').text

def test_pending_chat_only_returns_pending_work(auth):
    answer=auth.post('/api/v1/chat',json={'text':'¿Qué me falta de Eventos?'}).json()
    assert answer['sources'] and all(s['kind'] in ['task','reminder','event'] for s in answer['sources'])
    assert '2026-' not in answer['text']

def test_dates_are_normalized_to_utc(auth):
    response=auth.post('/api/v1/items',json={**task(),'due_at':'2026-12-01T09:00:00+02:00'})
    assert response.status_code==201
    assert response.json()['due_at']=='2026-12-01T07:00:00.000000+00:00'

def test_openclaw_queue_consent_model_and_ownership(auth,monkeypatch):
    import app.openclaw as bridge
    monkeypatch.setattr(settings,'openclaw_url','http://gateway:18789')
    monkeypatch.setattr(settings,'openclaw_token','test-gateway-secret')
    assert auth.post('/api/v1/agents/runs',json={'text':'Planifica mi día'}).status_code==409
    auth.patch('/api/v1/me',json={'ai_consent':True})
    assert auth.post('/api/v1/agents/runs',json={'agent_id':'arbitrary','text':'x'}).status_code==422
    auth.post('/api/v1/items',json=task())
    job=auth.post('/api/v1/agents/runs',json={'agent_id':'personal','text':'Mis tareas'}).json()
    calls=[]
    class FakeClient:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def stream(self,method,url,headers,json):
            return __import__('contextlib').nullcontext(self.post(url,headers,json))
        def post(self,url,headers,json):
            calls.append(json)
            assert headers['x-openclaw-model']=='test/model'
            assert json['model']=='openclaw/personal'
            assert json['tools']==[] and json['tool_choice']=='none'
            assert 'No llames herramientas del gateway' in json['messages'][0]['content']
            context=__import__('json').loads(json['messages'][1]['content'])
            assert all(r['area']=='personal' for r in context['sources'])
            return __import__('httpx').Response(200,request=__import__('httpx').Request('POST',url),json={'choices':[{'message':{'content':__import__('json').dumps({'reply':'Tarea revisada','source_ids':[context['sources'][0]['id'],'invented']})}}]})
    monkeypatch.setattr(bridge.httpx,'Client',FakeClient)
    bridge.process_jobs()
    result=auth.get('/api/v1/agents/runs/'+job['id']).json()
    assert result['status']=='completed' and result['reply']=='Tarea revisada'
    assert len(result['sources'])==1 and len(calls)==1
    with database.SessionLocal() as db:
        other=User(email='other@aros',password_hash=hash_password('other-secret'))
        db.add(other);db.commit()
        from app.security import issue_session
        secret=issue_session(db,other,__import__('fastapi').Response());db.commit()
    assert auth.get('/api/v1/agents/runs/'+job['id'],headers={'Authorization':'Bearer '+secret['access_token']}).status_code==404
    pending=auth.post('/api/v1/agents/runs',json={'text':'Otra consulta'}).json()
    assert auth.post('/api/v1/agents/runs/'+pending['id']+'/cancel',json={}).json()['status']=='cancelled'
    bridge.process_jobs();assert len(calls)==1


def test_followup_context_refreshes_owned_sources(auth):
    from app.ai import recent_history, conversation_context
    from app.database import Chat
    from sqlalchemy import select
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        other=User(email='private@local',password_hash=hash_password('other-password'))
        db.add(other);db.flush()
        original=Item(user_id=user.id,kind='task',area='learning',title='Entrega anterior',description='Texto actualizado')
        removed=Item(user_id=user.id,kind='task',area='personal',title='Archivada',status='archived')
        secret=Item(user_id=other.id,kind='note',area='personal',title='Dato privado')
        db.add_all([original,removed,secret]);db.flush()
        db.add(Chat(user_id=user.id,role='assistant',text='Respuesta antigua',sources=[{'id':original.id},{'id':removed.id},{'id':secret.id},{'id':'deleted'}]))
        db.add(Chat(user_id=other.id,role='user',text='CONVERSACION PRIVADA'))
        # Push the cited record outside the ordinary 100-record selection.
        for n in range(105): db.add(Item(user_id=user.id,kind='task',area='personal',title=f'Nueva tarea {n}',priority='urgent'))
        db.commit()
        history=recent_history(db,user)
        assert all(message.user_id==user.id for message in history)
        records=conversation_context(db,user,'Dame detalles de esa tarea',history)
        assert records[0].id==original.id and records[0].description=='Texto actualizado'
        assert len(records)<=16 and all(item.user_id==user.id and item.status!='archived' for item in records)
        db.delete(original);db.commit()
        assert original.id not in [item.id for item in conversation_context(db,user,'Dame detalles de esa tarea',history)]


def test_openclaw_chat_bounded_history_and_no_fallback(auth,monkeypatch):
    import json, httpx
    import app.openclaw as bridge
    from app.database import Chat
    from sqlalchemy import select
    monkeypatch.setattr(settings,'openclaw_url','http://gateway:18789')
    monkeypatch.setattr(settings,'openclaw_token','private-test-token')
    monkeypatch.setattr(settings,'openai_api_key','must-never-be-used')
    auth.patch('/api/v1/me',json={'ai_consent':True})
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        for n in range(12): db.add(Chat(user_id=user.id,role='user' if n%2==0 else 'assistant',text=f'Mensaje {n} '+('x'*4500),created_at=f'2026-10-01T10:{n:02}:00+00:00'))
        db.commit()
    calls=[]
    class FakeClient:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append(json)
            assert url.startswith('http://gateway:18789/')
            assert headers['x-openclaw-model']=='test/model'
            assert len(json['messages'])==10
            assert json['messages'][1]['content'].startswith('Mensaje 4 ')
            assert all(len(message['content'])<=4000 for message in json['messages'][1:-1])
            return httpx.Response(503,request=httpx.Request('POST',url))
    monkeypatch.setattr(bridge.httpx,'Client',FakeClient)
    response=auth.post('/api/v1/chat',json={'text':'¿Y esa tarea?'})
    assert response.status_code==502 and 'otro modelo' in response.json()['detail']
    assert len(calls)==1 and len(auth.get('/api/v1/chat').json())==12


def test_calendar_conflicts_free_slots_and_ownership(auth):
    from sqlalchemy import select
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        other=User(email='calendar-private@local',password_hash=hash_password('other-password'))
        db.add(other);db.flush()
        for title,start,end in [('Clase','09:00','11:00'),('Reunión','10:00','12:00'),('Estudio','12:00','13:00')]:
            db.add(Item(user_id=user.id,kind='event',area='learning',title=title,due_at=f'2026-12-01T{start}:00+00:00',data={'end_at':f'2026-12-01T{end}:00+00:00'}))
        db.add(Item(user_id=other.id,kind='event',area='personal',title='Privado',due_at='2026-12-01T07:00:00+00:00',data={'end_at':'2026-12-01T23:00:00+00:00'}))
        db.add(Item(user_id=user.id,kind='event',area='personal',title='Cancelado',status='cancelled',due_at='2026-12-01T07:00:00+00:00',data={'end_at':'2026-12-01T23:00:00+00:00'}))
        db.commit()
    response=auth.get('/api/v1/calendar/day?day=2026-12-01&zone=Europe/Madrid')
    assert response.status_code==200,response.text
    plan=response.json()
    assert len(plan['events'])==3 and len(plan['conflicts'])==1
    assert plan['conflicts'][0]['start']=='2026-12-01T10:00:00+00:00'
    assert [slot['minutes'] for slot in plan['free_slots']]==[120,480]
    assert 'Privado' not in response.text and 'Cancelado' not in response.text
    assert auth.get('/api/v1/calendar/day?zone=Invalid/Zone').status_code==422
    assert auth.get('/api/v1/calendar/day?start_hour=22&end_hour=8').status_code==422


def test_calendar_dst_and_exclusive_all_day_dates(auth):
    from sqlalchemy import select
    plan=auth.get('/api/v1/calendar/day?day=2027-03-28&start_hour=0&end_hour=24').json()
    assert plan['free_slots'][0]['minutes']==1380
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        db.add(Item(user_id=user.id,kind='event',area='personal',title='Día completo',due_at='2026-12-01T00:00:00+00:00',data={'all_day':True,'start_date':'2026-12-01','end_at':'2026-12-02'}));db.commit()
    plan=auth.get('/api/v1/calendar/day?day=2026-12-01&zone=America/Los_Angeles').json()
    assert len(plan['events'])==1 and plan['free_slots']==[]
    plan=auth.get('/api/v1/calendar/day?day=2026-12-02&zone=America/Los_Angeles').json()
    assert plan['events']==[] and plan['free_slots'][0]['minutes']==840


def test_calendar_unknown_duration_is_estimated(auth):
    from sqlalchemy import select
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        for n,metadata in enumerate([{}, {'end_at':'invalid'}]):
            db.add(Item(user_id=user.id,kind='event',area='personal',title=f'Sin duración {n}',due_at='2026-12-03T10:00:00+00:00',data=metadata))
        db.commit()
    plan=auth.get('/api/v1/calendar/day?day=2026-12-03').json()
    assert len(plan['conflicts'])==1 and all(event['estimated_duration'] for event in plan['events'])


def test_model_selector_consent_and_queued_model_snapshot(auth,monkeypatch):
    import app.model_preferences as choices
    import app.openclaw as bridge
    import httpx,json
    monkeypatch.setattr(settings,'openclaw_url','http://gateway:18789')
    monkeypatch.setattr(settings,'openclaw_token','private-test-token')
    selected='freellmapi/test-model'
    monkeypatch.setattr(choices,'available_models',lambda:([{'id':selected,'name':'Test model','provider':'FreeLLMAPI','detail':'Test'}],'Available'))
    assert auth.get('/api/v1/ai/preferences').json()['model']=='test/model'
    assert auth.patch('/api/v1/ai/preferences',json={'model':'freellmapi/unknown'}).status_code==409
    assert auth.patch('/api/v1/ai/preferences',json={'model':selected}).status_code==409
    auth.patch('/api/v1/me',json={'ai_consent':True})
    assert auth.patch('/api/v1/ai/preferences',json={'model':selected}).json()['model']==selected
    job=auth.post('/api/v1/agents/runs',json={'agent_id':'learning','text':'Mi entrega'}).json()
    assert job['model']==selected
    auth.patch('/api/v1/ai/preferences',json={'model':'test/model'})
    calls=[]
    class FakeClient:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append(headers['x-openclaw-model'])
            assert headers['x-openclaw-model']==selected and json['model']=='openclaw/learning'
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'{"reply":"Revisado","source_ids":[]}'}}]})
        def stream(self,method,url,headers,json):
            return __import__('contextlib').nullcontext(self.post(url,headers,json))
    monkeypatch.setattr(bridge.httpx,'Client',FakeClient)
    bridge.process_jobs()
    result=auth.get('/api/v1/agents/runs/'+job['id']).json()
    assert result['status']=='completed' and result['model']==selected and calls==[selected]
    assert auth.get('/api/v1/ai/preferences').json()['model']=='test/model'


def test_model_catalog_only_offers_registered_ready_models(auth,monkeypatch):
    import app.model_preferences as choices
    import httpx
    monkeypatch.setattr(settings,'freellm_url','http://freellmapi:3001')
    monkeypatch.setattr(settings,'freellm_token','must-not-leak')
    monkeypatch.setattr(settings,'freellm_models','known,auto,unavailable,claude-sonnet-4-6')
    class FakeClient:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,headers):
            assert url.endswith('/v1/models?available=true') and headers['Authorization']=='Bearer must-not-leak'
            return httpx.Response(200,request=httpx.Request('GET',url),json={'data':[{'id':'known'},{'id':'auto'},{'id':'unknown'},{'id':'claude-sonnet-4-6'}]})
    monkeypatch.setattr(choices.httpx,'Client',FakeClient)
    response=auth.get('/api/v1/ai/preferences')
    assert [model['id'] for model in response.json()['models']]==['test/model','freellmapi/known']
    assert 'must-not-leak' not in response.text

def test_agent_threads_followup_and_actions(auth,monkeypatch):
    import app.openclaw as bridge
    monkeypatch.setattr(settings,'openclaw_url','http://gateway')
    monkeypatch.setattr(settings,'openclaw_token','private-test')
    auth.patch('/api/v1/me',json={'ai_consent':True})
    calls=[]
    def completion(user,prompt,records,agent_id,history=None,model=None,action_collector=None):
        calls.append(history)
        action_collector['actions']=[{'type':'item.create','payload':task()}]
        return 'Propuesta lista',[]
    monkeypatch.setattr(bridge,'completion',completion)
    one=auth.post('/api/v1/agents/runs',json={'agent_id':'personal','text':'Prepara una tarea'}).json()
    assert auth.post('/api/v1/agents/runs',json={'agent_id':'personal','text':'Y otra'}).status_code==409
    bridge.process_jobs()
    two=auth.post('/api/v1/agents/runs',json={'agent_id':'personal','text':'Amplía esa tarea'}).json()
    bridge.process_jobs()
    assert calls[0]==[] and calls[1][0]=={'role':'user','content':'Prepara una tarea'}
    history=__import__('json').loads(calls[1][1]['content'])
    assert history['reply']=='Propuesta lista' and history['action_history'][0]['state']=='proposed'
    new_thread=__import__('uuid').uuid4().__str__()
    auth.post('/api/v1/agents/runs',json={'agent_id':'personal','thread_id':new_thread,'text':'Nueva conversación'})
    bridge.process_jobs();assert calls[-1]==[]
    result=auth.post('/api/v1/agents/runs/'+one['id']+'/actions/0',json={});assert result.status_code==200,result.text
    repeat=auth.post('/api/v1/agents/runs/'+one['id']+'/actions/0',json={}).json()
    assert repeat['id']==result.json()['id'] and repeat['already_applied']
    assert auth.get('/api/v1/items/'+repeat['id']).json()['title']==task()['title']
    assert len(auth.get('/api/v1/agents/runs?agent_id=personal&thread_id='+new_thread).json())==1

def test_private_tools_require_key_and_owner_consent(auth,monkeypatch):
    from cryptography.fernet import Fernet
    from app.channels import CONTROL
    from app.connectors import encode_tokens
    from app.database import Connector
    from sqlalchemy import select
    monkeypatch.setattr(settings,'openclaw_token','private-key')
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    path='/api/v1/internal/openclaw/resources'
    assert auth.post(path,json={'query':'pendientes'}).status_code==401
    headers={'Authorization':'Bearer private-key'}
    assert auth.post(path,json={'query':'pendientes'},headers=headers).status_code==403
    auth.patch('/api/v1/me',json={'ai_consent':True})
    assert auth.post(path,json={'query':'pendientes'},headers=headers).status_code==403
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email==settings.admin_email))
        db.add(Connector(id=CONTROL,user_id=user.id,name='WhatsApp',encrypted_tokens=encode_tokens({'phone':'+34600000000'}),sync_details={'desired':'enabled','pending':False}))
        db.commit()
    assert auth.post(path,json={'query':'pendientes'},headers={**headers,'X-Aros-Whatsapp-Sender':'+34600000001'}).status_code==403
    headers['X-Aros-Whatsapp-Sender']='+34600000000'
    assert auth.post(path,json={'query':'pendientes'},headers=headers).status_code==200
    action=auth.post('/api/v1/internal/openclaw/prepare',json={'type':'item.create','payload':task()},headers=headers)
    assert action.status_code==200 and action.json()['status']=='awaiting_review'
    run=auth.get('/api/v1/agents/runs/'+action.json()['run_id']).json()
    assert run['actions'] and run['status']=='completed'
    assert auth.post('/api/v1/internal/openclaw/prepare',json={'type':'exec','payload':{}},headers=headers).status_code==422

def test_channel_bridge_whitelisted_and_private(auth,monkeypatch):
    import app.channels as channel
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    calls=[]
    def rpc(method,params):
        calls.append((method,params))
        if method=='config.get': return {'hash':'config-hash'}
        if method=='channels.status': return {'channelAccounts':{'whatsapp':[{'connected':False,'linked':False,'phone':'private'}]}}
        return {'qrDataUrl':'data:image/png;base64,test','sessionKey':'session','connected':False,'credentials':'secret'}
    monkeypatch.setattr(channel,'rpc',rpc)
    assert 'private' not in auth.get('/api/v1/openclaw/channels').text
    assert auth.post('/api/v1/openclaw/whatsapp/start',json={'phone':'+34600000000'}).status_code==409
    auth.patch('/api/v1/me',json={'ai_consent':True})
    result=auth.post('/api/v1/openclaw/whatsapp/start',json={'phone':'+34600000000'})
    assert result.status_code==200 and 'credentials' not in result.text
    assert calls[-1][0]=='web.login.start' and calls[-1][1]['channel']=='whatsapp'

def test_google_application_setup_is_encrypted_and_scoped(auth,monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    client_id='test.apps.googleusercontent.com';secret='GOCSPX-private-test'
    callback=settings.public_origin+'/api/v1/oauth/google/callback'
    import json
    wrong={'web':{'client_id':client_id,'client_secret':secret,'redirect_uris':['https://wrong.test']}}
    assert auth.post('/api/v1/google/application',json={'credentials_json':json.dumps(wrong)}).status_code==422
    payload={'web':{**wrong['web'],'redirect_uris':[callback]}}
    result=auth.post('/api/v1/google/application',json={'credentials_json':json.dumps(payload)})
    assert result.status_code==200 and result.json()['configured']
    assert secret not in result.text and client_id not in result.text
    with database.SessionLocal() as db:
        from app.database import Connector
        from app.google_setup import credentials
        user=db.scalar(__import__('sqlalchemy').select(User).where(User.email==settings.admin_email))
        saved=db.get(Connector,('google-application',user.id))
        assert secret not in saved.encrypted_tokens
        assert credentials(db,user.id)==(client_id,secret)
    auth_url=auth.post('/api/v1/connectors/gmail/authorize',json={})
    assert auth_url.status_code==200 and client_id in auth_url.json()['url'] and secret not in auth_url.text
    assert 'google-application' not in [c['id'] for c in auth.get('/api/v1/connectors').json()]

def test_global_model_applies_to_gateway_channels(auth,monkeypatch):
    import app.channels as channel
    calls=[]
    monkeypatch.setattr(settings,'app_env','production')
    monkeypatch.setattr(settings,'openclaw_url','http://private-gateway')
    def rpc(method,params):
        calls.append((method,params));return {'hash':'version'} if method=='config.get' else {}
    monkeypatch.setattr(channel,'rpc',rpc)
    response=auth.patch('/api/v1/ai/preferences',json={'model':'test/model'})
    assert response.status_code==200
    assert calls[-1][0]=='config.patch'
    patch=__import__('json').loads(calls[-1][1]['raw'])
    assert len(patch['agents']['entries'])==7
    assert all(a['model']=={'primary':'test/model','fallbacks':[]} for a in patch['agents']['entries'].values())

def test_provider_quota_has_actionable_error_without_fallback(monkeypatch):
    import app.openclaw as bridge
    import httpx
    from fastapi import HTTPException
    from types import SimpleNamespace
    monkeypatch.setattr(settings,'openclaw_token','secret-not-for-client')
    calls=[]
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):
            calls.append(headers['x-openclaw-model'])
            return httpx.Response(429,request=httpx.Request('POST',url),json={'error':'rate_limited secret-not-for-client'})
    monkeypatch.setattr(bridge.httpx,'Client',Client)
    with pytest.raises(HTTPException) as error:
        bridge.completion(SimpleNamespace(ai_consent=True,ai_model='test/model'),'Hola',[],'aros')
    assert error.value.status_code==429 and 'secret-not-for-client' not in error.value.detail
    assert calls==['test/model']

@pytest.mark.parametrize('key,url,payload',[
 ('github','https://api.github.com/user/repos',[{'id':123,'full_name':'owner/project','description':'Example','html_url':'https://github.com/owner/project'}]),
 ('notion','https://api.notion.com/v1/search',{'results':[{'id':'page-1','url':'https://notion.so/page-1','properties':{'Title':{'type':'title','title':[{'plain_text':'Example page'}]}}}],'has_more':False}),
 ('vercel','https://api.vercel.com/v9/projects',{'projects':[{'id':'project-1','name':'Example project','framework':'nextjs'}],'pagination':{}}),
])
def test_account_connection_encrypted_sync_owner_and_disconnect(auth,monkeypatch,key,url,payload):
    import httpx
    from cryptography.fernet import Fernet
    from app import account_connectors
    from app.database import Connector
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,target,headers,params):
            assert target==url;assert headers['Authorization']=='Bearer private-test-credential';calls.append(target)
            return httpx.Response(200,request=httpx.Request('GET',target),json=payload)
        def post(self,target,headers,json):return self.get(target,headers,json)
    monkeypatch.setattr(account_connectors.httpx,'Client',FakeClient)
    response=auth.post('/api/v1/connectors/'+key+'/credential',json={'token':'private-test-credential'})
    assert response.status_code==200 and 'private-test-credential' not in response.text
    user=auth.get('/api/v1/me').json()
    with database.SessionLocal() as d:
        c=d.get(Connector,(key,user['id']));assert 'private-test-credential' not in c.encrypted_tokens
        other=User(email='account-other@aros',password_hash=hash_password('secret'));d.add(other);d.flush()
        assert d.get(Connector,(key,other.id)) is None
    for _ in range(2):assert auth.post('/api/v1/connectors/'+key+'/sync').status_code==200
    imported=[i for i in auth.get('/api/v1/items').json() if i['source']==key]
    # Identical reads preserve the resource version and its source references.
    assert len(imported)==1 and imported[0]['version']==1
    token=auth.post('/api/v1/confirmations',json={'action':'connector.disconnect','payload':{'id':key}}).json()['token']
    # Personal credentials must never be sent to Google's revoke endpoint.
    def forbid_revoke(*args,**kwargs):raise AssertionError('Wrong credential destination')
    monkeypatch.setattr(httpx,'post',forbid_revoke)
    assert auth.delete('/api/v1/connectors/'+key,headers={'X-Confirmation':token}).status_code==200
    assert len(calls)==3


def test_account_invalid_token_not_saved(auth,monkeypatch):
    import httpx
    from app import account_connectors
    from app.database import Connector
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,target,**kwargs):return httpx.Response(401,request=httpx.Request('GET',target))
    monkeypatch.setattr(account_connectors.httpx,'Client',FakeClient)
    assert auth.post('/api/v1/connectors/github/credential',json={'token':'invalid-test-credential'}).status_code==409
    assert auth.post('/api/v1/connectors/canva/credential',json={'token':'invalid-test-credential'}).status_code==404
    user=auth.get('/api/v1/me').json()
    with database.SessionLocal() as d:assert d.get(Connector,('github',user['id'])).encrypted_tokens is None


def test_weather_city_forecast_failure_cache_and_disconnect(auth,monkeypatch):
    import httpx
    from app import weather
    broken=False
    class FakeClient:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params):
            if url=='https://geocoding-api.open-meteo.com/v1/search':
                data={'results':[{'name':'Ciudad de ejemplo','country':'España','latitude':39.47,'longitude':-0.38}]}
            else:
                assert url=='https://api.open-meteo.com/v1/forecast';assert params['latitude']==39.47
                if broken:raise httpx.ConnectError('Offline')
                data={'timezone':'Europe/Madrid','current':{'temperature_2m':23.4,'apparent_temperature':22.1,'weather_code':2,'wind_speed_10m':12,'time':'2026-10-01T20:00'},'daily':{'temperature_2m_max':[25],'temperature_2m_min':[18],'precipitation_probability_max':[20]}}
            return httpx.Response(200,request=httpx.Request('GET',url),json=data)
    monkeypatch.setattr(weather.httpx,'Client',FakeClient)
    assert auth.get('/api/v1/weather').json()['configured'] is False
    city=auth.get('/api/v1/weather/cities?q=Ciudad de ejemplo').json()[0]
    assert auth.post('/api/v1/weather/location',json={**city,'latitude':91}).status_code==422
    assert auth.post('/api/v1/weather/location',json=city).status_code==200
    assert auth.post('/api/v1/connectors/weather/sync').status_code==200
    w=auth.get('/api/v1/weather').json();assert w['forecast']['temperature']==23.4 and not w['stale']
    assert w['location']==city and w['configured']
    broken=True
    assert auth.post('/api/v1/connectors/weather/sync').status_code==502
    cached=auth.get('/api/v1/weather').json()
    assert cached['forecast']['temperature']==23.4 and cached['stale']
    imported=[i for i in auth.get('/api/v1/items').json() if i['source']=='weather']
    assert len(imported)==1 and 'Open-Meteo' in imported[0]['description']
    token=auth.post('/api/v1/confirmations',json={'action':'connector.disconnect','payload':{'id':'weather'}}).json()['token']
    assert auth.delete('/api/v1/connectors/weather',headers={'X-Confirmation':token}).status_code==200
    assert auth.get('/api/v1/weather').json()['configured'] is False
