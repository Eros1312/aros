import json,itertools
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4
import httpx,pytest
from sqlalchemy import select
from test_api import client,auth,task
from app import openclaw as bridge
from app.database import AgentJob,AgentSchedule,User,SessionLocal
from app.config import settings
from app.agent_transport import decode_reply,reply_fragment
from app.agent_schedules import enqueue_due_reviews

@pytest.fixture
def enabled(auth,monkeypatch):
    monkeypatch.setattr(settings,'openclaw_url','http://gateway:18789')
    monkeypatch.setattr(settings,'openclaw_token','offline-token')
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    ticks=itertools.count()
    monkeypatch.setattr(bridge,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    return auth

def sse(content):return ('data: '+json.dumps({'choices':[{'delta':{'content':content}}]})+'\n\n').encode()

def install(monkeypatch,stream_factory,captured):
    original=httpx.Client
    def handle(request):
        payload=json.loads(request.content);captured.append(payload)
        assert payload['tools']==[] and payload['tool_choice']=='none' and payload['stream'] is True
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=stream_factory(payload))
    monkeypatch.setattr(bridge.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))

def test_public_reply_deltas_are_persisted_before_completion(enabled,monkeypatch):
    auth=enabled;auth.post('/api/v1/items',json=task())
    job=auth.post('/api/v1/agents/runs',json={'agent_id':'personal','text':'Revisa tareas'}).json();captured=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield sse('{"reply":"Hola')
            interim=auth.get('/api/v1/agents/runs/'+job['id']).json()
            assert interim['status']=='running' and interim['reply']=='Hola'
            assert interim['context_sources'] and any(e['type']=='receiving' for e in interim['events'])
            context=json.loads(captured[0]['messages'][-1]['content'])
            ident=context['sources'][0]['id']
            yield sse(' mundo","source_ids":'+json.dumps([ident,'invented'])+',"actions":[]}')
            yield b'data: [DONE]\n\n'
    install(monkeypatch,lambda payload:Stream(),captured);bridge.process_jobs()
    final=auth.get('/api/v1/agents/runs/'+job['id']).json()
    assert final['status']=='completed' and final['reply']=='Hola mundo'
    assert len(final['sources'])==1 and final['sources'][0]['id']!='invented'
    assert [e['type'] for e in final['events']]==['queued','context','request','receiving','completed']

def test_cancelling_closes_stream_and_cannot_resurrect_completed_action(enabled,monkeypatch):
    auth=enabled;job=auth.post('/api/v1/agents/runs',json={'text':'Prepara nota'}).json();captured=[];closed=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield sse('{"reply":"Parcial')
            assert auth.post('/api/v1/agents/runs/'+job['id']+'/cancel',json={}).status_code==200
            yield sse(' final","source_ids":[],"actions":[{"type":"item.create","payload":{"kind":"note","area":"personal","title":"Never apply"}}]}')
            yield b'data: [DONE]\n\n'
        def close(self):closed.append(True)
    install(monkeypatch,lambda payload:Stream(),captured);bridge.process_jobs()
    final=auth.get('/api/v1/agents/runs/'+job['id']).json()
    assert closed and final['status']=='cancelled' and final['actions']==[]
    assert final['events'][-1]['type']=='cancelled'

def test_unfinished_stream_keeps_partial_reply_but_reports_failure(enabled,monkeypatch):
    auth=enabled;job=auth.post('/api/v1/agents/runs',json={'text':'Hola'}).json();captured=[]
    class Broken(httpx.SyncByteStream):
        def __iter__(self):yield sse('{"reply":"Respuesta parcial')
    install(monkeypatch,lambda payload:Broken(),captured);bridge.process_jobs()
    final=auth.get('/api/v1/agents/runs/'+job['id']).json()
    assert final['status']=='failed' and final['reply']=='Respuesta parcial'
    assert final['events'][-1]['type']=='failed'

def test_truncated_terminal_delta_cannot_publish_actions(enabled,monkeypatch):
    job=enabled.post('/api/v1/agents/runs',json={'text':'Prepara una nota'}).json();captured=[]
    class Truncated(httpx.SyncByteStream):
        def __iter__(self):
            yield sse(json.dumps({'reply':'Nota preparada','source_ids':[],'actions':[{'type':'item.create','payload':{'kind':'note','area':'personal','title':'No aceptar truncada'}}]}))
            yield ('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'length'}]})+'\n\n').encode()
            yield b'data: [DONE]\n\n'
    install(monkeypatch,lambda payload:Truncated(),captured);bridge.process_jobs()
    result=enabled.get('/api/v1/agents/runs/'+job['id']).json()
    assert result['status']=='failed' and result['actions']==[]
    assert result['sources']==[] and result['events'][-1]['type']=='failed'

def test_inference_identity_is_fresh_and_history_is_owner_thread_scoped(enabled,monkeypatch):
    captured=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):yield sse('{"reply":"Conversación","source_ids":[]}');yield b'data: [DONE]\n\n'
    install(monkeypatch,lambda payload:Stream(),captured)
    for thread in ['main','main',str(uuid4())]:
        assert enabled.post('/api/v1/agents/runs',json={'agent_id':'learning','thread_id':thread,'text':'Continúa'}).status_code==202
        bridge.process_jobs()
    assert len({payload['user'] for payload in captured[:3]})==3
    assert captured[1]['messages'][2]['content']=='Conversación'
    assert len(captured[1]['messages'])==4 and len(captured[2]['messages'])==2
    with SessionLocal() as db:
        other=User(email='isolated@example.test',password_hash='unused',ai_consent=True);db.add(other);db.flush()
        db.add(AgentJob(user_id=other.id,agent_id='learning',thread_id='main',model=other.ai_model,prompt='Otro usuario'));db.commit()
    bridge.process_jobs()
    assert captured[3]['user'] not in [payload['user'] for payload in captured[:3]]
    assert json.loads(captured[3]['messages'][-1]['content'])['sources']==[]
    assert 'Aprendizaje' in captured[0]['messages'][0]['content']

def test_reply_parser_handles_envelope_without_inventing_citations():
    row=SimpleNamespace(id='one',title='Documento',area='learning',kind='document')
    reply,sources,actions=decode_reply('Resultado: {"reply":"Sí","source_ids":["one","missing","one"]} fin',[row])
    assert reply=='Sí' and len(sources)==1 and actions==[]
    assert reply_fragment('{"reply":"Primera\\nsegunda')=='Primera\nsegunda'
    assert reply_fragment('{"reply":"Code \\u00')=='Code '

def test_reviews_require_consent_and_are_bounded_pauseable_and_area_scoped(auth,enabled):
    assert auth.patch('/api/v1/agents/schedules/learning',json={'enabled':True,'interval_minutes':1}).status_code==422
    plan=auth.patch('/api/v1/agents/schedules/learning',json={'enabled':True,'interval_minutes':720}).json()
    assert enqueue_due_reviews()==1 and enqueue_due_reviews()==0
    with SessionLocal() as db:
        jobs=list(db.scalars(select(AgentJob).where(AgentJob.thread_id==plan['thread_id'])))
        assert len(jobs)==1 and jobs[0].agent_id=='learning' and jobs[0].actions==[]
        assert 'lecturas, notas' in jobs[0].prompt
    assert auth.patch('/api/v1/agents/schedules/learning',json={'enabled':False,'interval_minutes':720}).status_code==200
    assert enqueue_due_reviews()==0

def test_reviews_never_send_data_after_consent_revocation(enabled,monkeypatch):
    auth=enabled
    assert auth.patch('/api/v1/agents/schedules/personal',json={'enabled':True,'interval_minutes':60}).status_code==200
    monkeypatch.setattr('app.channels.rpc',lambda method,params:{'hash':'offline-test'})
    assert auth.patch('/api/v1/me',json={'ai_consent':False}).status_code==200
    assert enqueue_due_reviews()==0
    assert auth.patch('/api/v1/agents/schedules/personal',json={'enabled':True,'interval_minutes':60}).status_code==409


def test_legacy_completion_has_fresh_opaque_gateway_identity(enabled,monkeypatch):
    captured=[];original=httpx.Client
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"reply":"Hola","source_ids":[]}'}}]})
    monkeypatch.setattr(bridge.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        bridge.completion(user,'Hola',[],'personal');bridge.completion(user,'Hola de nuevo',[],'personal')
        assert captured[0]['user']!=captured[1]['user'] and user.id not in captured[0]['user']
        assert all(len(payload['messages'])==2 for payload in captured)
