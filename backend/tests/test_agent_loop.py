"""Offline model decisions drive real durable tools; no gateway calls."""
import copy,json
from uuid import uuid4
from datetime import datetime,timedelta,timezone
import httpx,pytest
from sqlalchemy import select,update,true
from test_api import client,auth
from app import agent_loop as loop
from app.database import AgentJob,Item,User,Connector,SessionLocal,now
from app.main import app

if not any(getattr(r,'path','')=='/api/v1/agents/autonomous' for r in app.routes):
    prior=len(app.router.routes)
    app.include_router(loop.router,prefix='/api/v1/agents')
    added=app.router.routes[prior:]
    app.router.routes[:]=added+app.router.routes[:prior]

@pytest.fixture
def enabled(auth):
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    return auth

def resource(foreign=False,source='manual'):
    with SessionLocal() as db:
        u=db.scalar(select(User).where(User.email=='aros@local'))
        if foreign:u=User(email=str(uuid4())+'@local',password_hash='unused');db.add(u);db.flush()
        item=Item(user_id=u.id,kind='document',area='learning',title='ATP Programación',description='La ATP sintetasa produce ATP mediante un gradiente de protones.',source=source,status='active');db.add(item);db.commit();return item.id

def command(id,**extra):
    return {'goal':'Preparar un borrador útil sobre ATP sintetasa','title':'Estudio ATP','area':'learning','item_ids':[id],'authorize_internal_write':True,'idempotency_key':str(uuid4()),**extra}

def enqueue(api,payload):
    r=api.post('/api/v1/agents/autonomous',json=payload);assert r.status_code==202,r.text
    return r.json()['id']

def state(id):
    with SessionLocal() as db:return copy.deepcopy(db.get(AgentJob,id).execution)

def step(id):
    with SessionLocal() as db:
        job=db.get(AgentJob,id)
        if job.status!='pending':return
        lease=str(uuid4());job.status='running';job.lease_id=lease;job.started_at=now();job.heartbeat_at=now();job.attempt_count+=1;db.commit()
        loop.process_loop_step(db,job,lease)

def decisions(monkeypatch,values):
    calls=[]
    def fake(job,e):
        calls.append(copy.deepcopy(e))
        assert e['phase']=='inference' and e['budget']['model_calls']==len(calls)
        return json.dumps(values[len(calls)-1](e) if callable(values[len(calls)-1]) else values[len(calls)-1])
    monkeypatch.setattr(loop,'infer',fake);return calls

def tool(name,args):return {'kind':'tool','tool':name,'arguments':args}
def complete(monkeypatch,api,source,summary='Puedes revisar la síntesis.'):
    calls=decisions(monkeypatch,[tool('resources.search',{'query':'ATP'}),tool('resources.read',{'ids':[source]}),tool('note.write',{'title':'Síntesis ATP','body':'Borrador de estudio: ATP sintetasa y gradiente de protones.','source_ids':[source]}),lambda e:tool('artifact.verify',{'artifact_id':e['artifact_id']}),{'kind':'finish','summary':summary}])
    id=enqueue(api,command(source))
    for _ in range(9):step(id)
    assert state(id)['state']=='completed'
    return id,calls

def test_real_iteration_observation_draft_verification_and_undo(enabled,monkeypatch):
    source=resource();id,calls=complete(monkeypatch,enabled,source);e=state(id)
    assert len(calls)==5 and e['budget']['model_calls']==5 and e['budget']['tool_calls']==4
    assert calls[1]['observations'][0]['resources'][0]['id']==source
    assert 'excerpt' not in calls[1]['observations'][0]['resources'][0]
    assert 'gradiente' in calls[2]['observations'][1]['resources'][0]['excerpt']
    assert e['verified'] and e['phase']=='verified'
    r=enabled.get(f'/api/v1/agents/autonomous/{id}/artifact');assert r.status_code==200,r.text
    artifact=r.json();assert artifact['source']=='agent-autonomous' and artifact['data']['review_state']=='draft'
    assert artifact['data']['source_references'][0]['id']==source
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/undo',json={'version':99}).status_code==409
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/undo',json={'version':artifact['version']}).status_code==200
    assert enabled.get(f'/api/v1/agents/autonomous/{id}/artifact').status_code==409
    with SessionLocal() as db:assert db.get(Item,source).version==1 and db.get(Item,e['artifact_id']).status=='archived'

def test_idempotency_scope_consent_owner_and_typed_contract(enabled,monkeypatch):
    payload=command(resource());id=enqueue(enabled,payload);assert enqueue(enabled,payload)==id
    assert enabled.post('/api/v1/agents/autonomous',json={**payload,'goal':'Otro objetivo diferente'}).status_code==409
    assert enabled.post('/api/v1/agents/autonomous',json={**payload,'authorize_internal_write':False}).status_code==409
    assert enabled.post('/api/v1/agents/autonomous',json={**payload,'iteration_limit':13}).status_code==422
    foreign=resource(foreign=True)
    decisions(monkeypatch,[tool('resources.read',{'ids':[foreign]}),{'kind':'ask','question':'Selecciona una fuente autorizada.'}])
    step(id);step(id);assert state(id)['observations'][-1]['error_code']=='scope_denied'
    step(id);assert state(id)['state']=='waiting_information' and state(id)['artifact_id'] is None
    for raw in [tool('note.write',{'title':'x','body':'long enough content here','source_ids':[payload['item_ids'][0]],'url':'https://evil.test'}),{'kind':'tool','tool':'shell','arguments':{}},{'kind':'finish','summary':'OK','arguments':{'host':'x'}}]:
        with pytest.raises((ValueError,TypeError)):loop.parse_decision(json.dumps(raw))

def test_false_finish_invalid_decision_and_budget_do_not_complete(enabled,monkeypatch):
    calls=decisions(monkeypatch,[{'kind':'finish','summary':'Hecho.'},{'kind':'tool','tool':'shell','arguments':{}},{'kind':'finish','summary':'Hecho.'}])
    id=enqueue(enabled,command(resource(),iteration_limit=3))
    for _ in range(4):step(id)
    e=state(id);assert e['state']=='failed' and e['error_code']=='budget_exhausted' and e['artifact_id'] is None
    assert len(calls)==3 and len(e['observations'])==3

def test_revocation_during_model_call_no_effect_or_next_call(enabled,monkeypatch):
    source=resource(source='gmail')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));c=db.get(Connector,('gmail',user.id))
        if not c:c=Connector(id='gmail',user_id=user.id,status='connected');db.add(c)
        else:c.status='connected'
        db.commit()
    calls=decisions(monkeypatch,[tool('resources.read',{'ids':[source]})]);id=enqueue(enabled,command(source));step(id);step(id)
    def revoked(job,e):
        with SessionLocal() as db:
            c=db.get(Connector,('gmail',job.user_id));c.status='revoked';db.commit()
        return json.dumps(tool('note.write',{'title':'x','body':'Must never be saved due to revoked source.','source_ids':[source]}))
    monkeypatch.setattr(loop,'infer',revoked);step(id)
    assert state(id)['state']=='failed' and state(id)['error_code']=='source_unavailable'
    with SessionLocal() as db:assert not db.scalar(select(Item.id).where(Item.external_id==id,Item.source=='agent-autonomous'))

def test_cancel_during_call_and_reservation_visible_before_infer(enabled,monkeypatch):
    id=enqueue(enabled,command(resource()))
    def cancelled(job,e):
        with SessionLocal() as db:
            row=db.get(AgentJob,id);assert row.execution['phase']=='inference' and row.execution['budget']['model_calls']==1
            row.status='cancelled';db.commit()
        return json.dumps({'kind':'finish','summary':'No debe entregarse.'})
    monkeypatch.setattr(loop,'infer',cancelled);step(id)
    with SessionLocal() as db:assert db.get(AgentJob,id).status=='cancelled' and not db.get(AgentJob,id).reply

def test_uncertain_inference_no_automatic_retry_and_safe_tool_recovery(enabled,monkeypatch):
    calls=[]
    def fail(job,e):calls.append(1);raise httpx.ReadTimeout('interrupted')
    monkeypatch.setattr(loop,'infer',fail);id=enqueue(enabled,command(resource()));step(id);step(id)
    assert len(calls)==1 and state(id)['error_code']=='inference_uncertain'
    source=resource();decisions(monkeypatch,[tool('resources.read',{'ids':[source]})]);id=enqueue(enabled,command(source));step(id)
    with SessionLocal() as db:
        job=db.get(AgentJob,id);job.status='running';job.lease_id='stale';db.commit();loop.recover_loops(db,true())
    assert state(id)['recovery_count']==1 and state(id)['phase']=='tool'
    step(id);assert state(id)['budget']['model_calls']==1 and state(id)['budget']['tool_calls']==1
    with SessionLocal() as db:
        job=db.get(AgentJob,id);e=copy.deepcopy(job.execution);e['phase']='inference';job.execution=e;job.status='running';db.commit();loop.recover_loops(db,true())
    assert state(id)['state']=='failed' and state(id)['error_code']=='inference_uncertain'

def test_provider_backoff_two_retries_no_fallback(enabled,monkeypatch):
    count=[]
    def limited(job,e):
        count.append(e['budget']['model_calls']);assert job.model==loop.MODEL
        raise httpx.HTTPStatusError('limited',request=httpx.Request('POST','https://gateway.test'),response=httpx.Response(429))
    monkeypatch.setattr(loop,'infer',limited);id=enqueue(enabled,command(resource()))
    for i in range(3):
        step(id)
        with SessionLocal() as db:
            row=db.get(AgentJob,id)
            if i<2:assert row.retry_after is not None and row.status=='pending'
    assert count==[1,2,3] and state(id)['rate_limit_retries']==2 and state(id)['state']=='failed'

def test_artifact_changed_and_revoked_are_not_reopened(enabled,monkeypatch):
    source=resource();id,_=complete(monkeypatch,enabled,source)
    with SessionLocal() as db:item=db.get(Item,source);item.description='Contenido modificado';db.commit()
    assert enabled.get(f'/api/v1/agents/autonomous/{id}/artifact').status_code==409
    with SessionLocal() as db:
        job=db.get(AgentJob,id);user=db.get(User,job.user_id);dto=loop.public(job,db,user)
        assert 'observations' not in dto['execution'] and 'selected_sources' not in dto['execution']

def test_read_required_and_repetition_observed_not_applied(enabled,monkeypatch):
    source=resource();body='Borrador que requiere lectura previa de la fuente.'
    calls=decisions(monkeypatch,[tool('note.write',{'title':'ATP','body':body,'source_ids':[source]}),tool('resources.read',{'ids':[source]}),tool('resources.read',{'ids':[source]}),tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'Necesito una indicación adicional.'}])
    id=enqueue(enabled,command(source))
    for _ in range(9):step(id)
    e=state(id);assert e['state']=='waiting_information' and e['artifact_id'] is None
    assert e['observations'][0]['error_code']=='sources_not_read'
    assert e['observations'][-1]['error_code']=='repeated_tool' and e['budget']['tool_calls']==2
    assert len(calls)==5

def test_deadline_and_consent_block_before_model(enabled,monkeypatch):
    monkeypatch.setattr(loop,'infer',lambda *args:pytest.fail('No inference after expired budget'))
    id=enqueue(enabled,command(resource()))
    with SessionLocal() as db:
        job=db.get(AgentJob,id);e=copy.deepcopy(job.execution);e['budget']['deadline_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();job.execution=e;db.commit()
    step(id);assert state(id)['error_code']=='budget_exhausted' and state(id)['budget']['model_calls']==0
    id=enqueue(enabled,command(resource()))
    assert enabled.patch('/api/v1/me',json={'ai_consent':False}).status_code==200
    step(id);assert state(id)['budget']['model_calls']==0

def test_transport_current_gateway_fixed_luna_isolated_iterations(enabled,monkeypatch):
    id=enqueue(enabled,command(resource()));e=state(id);payloads=[]
    monkeypatch.setattr(loop.settings,'openclaw_token','offline-test-token')
    def transport(client,url,headers,payload,progress):
        assert headers['x-openclaw-model']==loop.MODEL and payload['model']=='openclaw/aros-planner'
        assert payload['tools']==[] and payload['tool_choice']=='none' and progress is not None
        assert 'browser' not in payload and 'Authorization' not in payload['messages'][1]['content']
        context=json.loads(payload['messages'][1]['content']);protocol=context['execution_protocol']
        assert protocol['available'] is True and protocol['native_gateway_tools'] is False
        assert set(protocol['tools'])==set(loop.TOOLS) and protocol['capability']==loop.CAPABILITY
        assert protocol['tools']['note.write']['additionalProperties'] is False
        assert 'backend autenticado de Aros' in payload['messages'][0]['content']
        assert 'No llames herramientas nativas' in payload['messages'][0]['content']
        payloads.append(payload);return '{"kind":"ask","question":"Más información."}'
    monkeypatch.setattr(loop,'request_text',transport)
    with SessionLocal() as db:
        job=db.get(AgentJob,id)
        e['budget']['model_calls']=1;loop.infer(job,e)
        e['budget']['model_calls']=2;loop.infer(job,e)
    assert payloads[0]['user']!=payloads[1]['user'] and id not in payloads[0]['user']

def test_draft_freezes_sources_and_versioned_undo_rejects_edits(enabled,monkeypatch):
    source=resource();id,_=complete(monkeypatch,enabled,source);e=state(id)
    with SessionLocal() as db:
        job=db.get(AgentJob,id);user=db.get(User,job.user_id)
        with pytest.raises(loop.CapabilityBlocked,match='ya está guardado'):loop.tool_step(db,user,job,e,tool('resources.read',{'ids':[source]}))
        item=db.get(Item,e['artifact_id']);item.description='Edición manual de este borrador';item.version+=1;db.commit();version=item.version
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/undo',json={'version':version}).status_code==409
    assert enabled.get(f'/api/v1/agents/autonomous/{id}/artifact').status_code==409

def test_existing_worker_dispatches_model_and_tool_transitions(enabled,monkeypatch):
    from app.openclaw import process_jobs
    source=resource();calls=decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'¿Quieres comparar más fuentes?'}])
    id=enqueue(enabled,command(source))
    for _ in range(3):process_jobs(review_only=False,limit=1)
    assert state(id)['state']=='waiting_information' and state(id)['budget']['tool_calls']==1 and len(calls)==2

def test_uncommitted_effect_rolls_back_then_recovers_once(enabled,monkeypatch):
    source=resource();decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),tool('note.write',{'title':'ATP','body':'Borrador completo basado en la fuente autorizada.','source_ids':[source]})])
    id=enqueue(enabled,command(source));step(id);step(id);step(id)
    with SessionLocal() as db:
        job=db.get(AgentJob,id);e=copy.deepcopy(job.execution);user=db.get(User,job.user_id)
        loop.tool_step(db,user,job,e,e['pending_decision']);artifact=e['artifact_id'];assert db.get(Item,artifact)
        db.rollback() # Simulates process loss before atomic effect/state commit.
    with SessionLocal() as db:assert db.get(Item,artifact) is None
    step(id);assert state(id)['artifact_id']==artifact
    with SessionLocal() as db:assert len(list(db.scalars(select(Item.id).where(Item.external_id==id,Item.source=='agent-autonomous'))))==1

def test_new_read_after_write_is_recoverable_then_verify_finishes(enabled,monkeypatch):
    source=resource();values=[tool('resources.read',{'ids':[source]}),tool('note.write',{'title':'ATP','body':'Borrador completo sobre ATP y protones.','source_ids':[source]}),tool('resources.read',{'ids':[source]}),lambda e:tool('artifact.verify',{'artifact_id':e['artifact_id']}),{'kind':'finish','summary':'Revisión terminada.'}]
    decisions(monkeypatch,values);id=enqueue(enabled,command(source))
    for _ in range(9):step(id)
    e=state(id);assert e['state']=='completed' and e['verified']
    assert e['observations'][2]['error_code']=='draft_exists'

@pytest.mark.parametrize('change',['changed','revoked'])
@pytest.mark.parametrize('result',['ask','completed'])
def test_changed_or_revoked_sources_hide_private_dto_copies_and_artifacts(enabled,monkeypatch,change,result):
    marker='EXTRACTO_PRIVADO_NO_PUBLICAR_445';source=resource(source='gmail')
    with SessionLocal() as db:
        item=db.get(Item,source);item.description+=' '+marker
        connector=db.get(Connector,('gmail',item.user_id));connector.status='connected';db.commit()
    if result=='ask':
        decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':marker+' ¿Quieres una síntesis?'}])
        id=enqueue(enabled,command(source));step(id);step(id);step(id)
    else:id,_=complete(monkeypatch,enabled,source,summary=marker)
    # Include every private channel, including a persisted tool decision body.
    with SessionLocal() as db:
        job=db.get(AgentJob,id);e=copy.deepcopy(job.execution)
        e['pending_decision']=tool('note.write',{'title':'x','body':marker,'source_ids':[source]});job.execution=e
        e['clarifications']=[{'idempotency_key':str(uuid4()),'content_hash':'test','question':marker,'text':'Sí','at':now()}];job.execution=e
        job.error=marker;job.events=[*(job.events or []),{'type':'error','text':marker}]
        item=db.get(Item,source)
        if change=='changed':item.description+=' changed' # Hash catches changes without version increment.
        else:db.get(Connector,('gmail',item.user_id)).status='revoked'
        db.commit();artifact_id=e.get('artifact_id')
    for url in ['/api/v1/agents/runs',f'/api/v1/agents/runs/{id}','/api/v1/agents/runtime']:
        response=enabled.get(url);assert response.status_code==200,response.text
        assert marker not in response.text
        if '/runtime' not in url:
            data=response.json();job=next(j for j in data if j['id']==id) if isinstance(data,list) else data
            assert job['resources_unavailable'] and job['reply']==''
            assert not any(key in job['execution'] for key in ['observations','selected_sources','pending_decision','clarifications'])
    if artifact_id:
        assert enabled.get(f'/api/v1/items/{artifact_id}').status_code==404
        assert artifact_id not in {row['id'] for row in enabled.get('/api/v1/items').json()}
        assert enabled.get(f'/api/v1/agents/autonomous/{id}/artifact').status_code==409

def test_answer_resumes_same_scope_budget_and_is_idempotent(enabled,monkeypatch):
    source=resource();decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'¿Quieres una síntesis breve?'},{'kind':'ask','question':'¿Necesitas ejemplos?'}])
    id=enqueue(enabled,command(source));step(id);step(id);step(id);before=state(id)
    payload={'text':'Sí, breve.','idempotency_key':str(uuid4()),'version':before['state_version']}
    response=enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json=payload);assert response.status_code==202,response.text
    after=state(id);assert after['state']=='queued' and after['phase']=='decide'
    assert before['scope']==after['scope'] and before['authorization']==after['authorization'] and before['budget']==after['budget']
    assert after['clarifications'][0]['question']=='¿Quieres una síntesis breve?' and after['clarification_count']==1
    assert 'clarifications' not in response.json()['execution']
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json=payload).status_code==202
    assert len(state(id)['clarifications'])==1
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json={**payload,'text':'Otra respuesta'}).status_code==409
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json={'text':'Nueva','idempotency_key':str(uuid4()),'version':state(id)['state_version']}).status_code==409
    step(id);assert state(id)['state']=='waiting_information' and state(id)['budget']['model_calls']==3

@pytest.mark.parametrize('blocked',['deadline','iterations','consent','source','owner'])
def test_answer_requires_current_permission_budget_and_owner(enabled,monkeypatch,blocked):
    source=resource();decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'¿Continuamos?'}]);id=enqueue(enabled,command(source));step(id);step(id);step(id)
    with SessionLocal() as db:
        job=db.get(AgentJob,id);e=copy.deepcopy(job.execution)
        if blocked=='deadline':e['budget']['deadline_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        elif blocked=='iterations':e['budget']['model_calls']=e['budget']['model_call_limit']
        elif blocked=='consent':db.get(User,job.user_id).ai_consent=False
        elif blocked=='source':db.get(Item,source).status='archived'
        elif blocked=='owner':
            other=User(email=str(uuid4())+'@local',password_hash='unused');db.add(other);db.flush();job.user_id=other.id
        job.execution=e;db.commit()
    payload={'text':'Sí','idempotency_key':str(uuid4()),'version':state(id)['state_version']}
    response=enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json=payload)
    assert response.status_code==(404 if blocked=='owner' else 409),response.text
    assert not state(id).get('clarifications')

def test_answer_max_three_and_transport_keeps_question_answer_context(enabled,monkeypatch):
    original_infer=loop.infer
    source=resource();decisions(monkeypatch,[{'kind':'ask','question':'¿Continuamos?'}]*4);id=enqueue(enabled,command(source))
    for i in range(3):
        step(id);response=enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json={'text':f'Sí {i}','idempotency_key':str(uuid4()),'version':state(id)['state_version']});assert response.status_code==202,response.text
    step(id)
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json={'text':'Cuarta','idempotency_key':str(uuid4()),'version':state(id)['state_version']}).status_code==409
    monkeypatch.setattr(loop.settings,'openclaw_token','offline-test-token')
    def transport(client,url,headers,payload,progress):
        context=json.loads(payload['messages'][1]['content']);assert context['user_clarifications']==[{'question':'¿Continuamos?','answer':f'Sí {i}'} for i in range(3)]
        assert context['budget']['model_calls']==4
        return '{"kind":"ask","question":"OK"}'
    monkeypatch.setattr(loop,'request_text',transport)
    with SessionLocal() as db:
        job=db.get(AgentJob,id)
        # The test's inference mock is replaced by the actual transport builder.
        original_infer(job,copy.deepcopy(job.execution))

def test_answer_stale_question_version_cannot_answer_new_question(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'ask','question':'Primera pregunta'},{'kind':'ask','question':'Segunda pregunta'}])
    id=enqueue(enabled,command(resource()));step(id);old=state(id)['state_version']
    payload={'text':'Sí a la primera','idempotency_key':str(uuid4()),'version':old}
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json=payload).status_code==202
    step(id);before=state(id)
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json={'text':'Respuesta de pestaña antigua','idempotency_key':str(uuid4()),'version':old}).status_code==409
    assert state(id)==before
    # An uncertain delivery retry refers to its original answer, not ask #2.
    assert enabled.post(f'/api/v1/agents/autonomous/{id}/answer',json=payload).status_code==202
    assert len(state(id)['clarifications'])==1
