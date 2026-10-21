"""Ordinary chat uses the durable executor, including real typed effects."""
import copy,json
from uuid import uuid4
from datetime import datetime,timedelta,timezone
import httpx,pytest
from pydantic import ValidationError
from sqlalchemy import select,true
from test_api import client,auth,token
from app import conversation_engine as engine
from app import openclaw as bridge
from app.config import settings
from app.database import AgentJob,Item,User,Connector,Chat,SessionLocal,now

@pytest.fixture
def enabled(auth,monkeypatch):
    auth.patch('/api/v1/me',json={'ai_consent':True})
    monkeypatch.setattr(settings,'openclaw_url','http://gateway.test')
    monkeypatch.setattr(settings,'openclaw_token','test-only')
    return auth

def resource(area='university',source='manual',foreign=False,description='Las proteínas se desnaturalizan a 75 °C. Valencia es la ciudad del ejemplo.'):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        if foreign:user=User(email=str(uuid4())+'@other.local',password_hash='unused');db.add(user);db.flush()
        item=Item(user_id=user.id,area=area,kind='document',source=source,title='Proteínas y estudio',description=description,status='active');db.add(item);db.commit();return item.id

def post(api,text='Resume este documento',**extra):
    payload={'text':text,'integrated':True,'idempotency_key':str(uuid4()),**extra}
    result=api.post('/api/v1/chat',json=payload);assert result.status_code==202,result.text
    return result.json()['id'],payload

def state(id):
    with SessionLocal() as db:return copy.deepcopy(db.get(AgentJob,id).execution)

def step(id):
    with SessionLocal() as db:
        job=db.get(AgentJob,id)
        if job.status!='pending':return
        lease=str(uuid4());job.status='running';job.lease_id=lease;job.started_at=now();job.heartbeat_at=now();job.attempt_count+=1;db.commit()
        engine.process_step(db,job,lease)

def tool(name,args):return {'kind':'tool','tool':name,'arguments':args}

def decisions(monkeypatch,values,history=None):
    calls=[]
    def fake(job,e,messages):
        calls.append(copy.deepcopy(e))
        if history is not None:history.append(copy.deepcopy(messages))
        value=values[len(calls)-1]
        return json.dumps(value(e) if callable(value) else value)
    monkeypatch.setattr(engine,'infer',fake);return calls

def run(id,count=25):
    for _ in range(count):step(id)

def create_args(title='Recordatorio de estudio'):
    return {'kind':'reminder','area':'personal','title':title,'description':'Repasar proteínas, según la información leída.','operation_key':'study-reminder'}

def complete_write(api,monkeypatch,source):
    calls=decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),tool('items.create',create_args()),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'He guardado el recordatorio interno y lo he verificado.','source_ids':[source]}])
    id,payload=post(api,'Lee este documento y crea un recordatorio interno para repasarlo',item_ids=[source])
    run(id);assert state(id)['state']=='completed'
    return id,calls

def test_chat_reads_and_finishes_without_forcing_artifact(enabled,monkeypatch):
    source=resource();calls=decisions(monkeypatch,[tool('resources.search',{'query':'proteínas'}),tool('resources.read',{'ids':[source]}),{'kind':'finish','summary':'La fuente explica desnaturalización a 75 °C.','source_ids':[source]}])
    id,payload=post(enabled,item_ids=[source]);run(id)
    e=state(id);assert e['state']=='completed' and e['artifacts']==[] and len(calls)==3
    dto=enabled.get('/api/v1/agents/runs/'+id).json()
    assert dto['thread_id']=='chat' and dto['sources'][0]['id']==source
    assert all(k not in dto['execution'] for k in ['observations','selected_sources','history_sources'])
    messages=enabled.get('/api/v1/chat').json();mine=[m for m in messages if m.get('job_id')==id]
    assert [m['role'] for m in mine]==['user','assistant'] and mine[1]['id']==e['reply_message_id']
    assert len(calls[-1]['observations'][1]['resources'][0]['excerpt'])>20

def test_chat_create_verify_history_and_owner_boundaries(enabled,monkeypatch):
    source=resource();id,calls=complete_write(enabled,monkeypatch,source);e=state(id)
    assert len(calls)==4 and e['verified'] and e['artifacts'][0]['verified']
    artifact=e['artifacts'][0]['id'];result=enabled.get('/api/v1/items/'+artifact).json()
    assert result['kind']=='reminder' and result['source']=='agent-conversation'
    assert result['data']['source_references'][0]['id']==source
    dto=enabled.get('/api/v1/agents/runs/'+id).json();assert dto['actions'][0]['applied_id']==artifact
    messages=[];decisions(monkeypatch,[{'kind':'finish','summary':'El recordatorio ya se guardó; puedes abrirlo en Personal.'}],messages)
    following,payload=post(enabled,'¿Qué acabas de guardar?');run(following)
    assert any('recordatorio' in m['content'].lower() for m in messages[0] if m['role']=='assistant')
    with SessionLocal() as db:
        row=db.get(Item,source);row.status='archived';db.commit()
    assert enabled.get('/api/v1/items/'+artifact).status_code==404
    revoked=enabled.get('/api/v1/agents/runs/'+id).json()
    assert revoked['resources_unavailable'] and revoked['reply']=='' and revoked['execution']['artifacts']==[]
    assert '75 °C' not in enabled.get('/api/v1/agents/runs').text
    history=enabled.get('/api/v1/chat').json();assert next(m for m in history if m['id']==e['reply_message_id'])['resources_unavailable']

def test_idempotency_and_single_active_thread(enabled):
    id,payload=post(enabled,'Hola Aros')
    assert enabled.post('/api/v1/chat',json=payload).json()['id']==id
    assert enabled.post('/api/v1/chat',json={**payload,'text':'Otro mensaje'}).status_code==409
    assert enabled.post('/api/v1/chat',json={'text':'Siguiente','integrated':True}).status_code==409
    assert len([m for m in enabled.get('/api/v1/chat').json() if m.get('job_id')==id])==1
    assert enabled.post('/api/v1/agents/runs/'+id+'/cancel').status_code==200
    assert enabled.post('/api/v1/chat',json={'text':'Siguiente','integrated':True}).status_code==202

def test_integrated_agent_cross_area_and_question_followup(enabled,monkeypatch):
    personal=resource('personal');university=resource('university');messages=[]
    decisions(monkeypatch,[tool('resources.read',{'ids':[personal,university]}),{'kind':'ask','question':'¿A qué hora quieres repasarlo?'}],messages)
    result=enabled.post('/api/v1/agents/runs',json={'agent_id':'university','text':'Compara mis documentos de universidad y personal','integrated':True});assert result.status_code==202,result.text
    id=result.json()['id'];run(id);assert state(id)['state']=='waiting_information'
    dto=enabled.get('/api/v1/agents/runs/'+id).json();assert dto['status']=='completed' and not dto['resources_unavailable']
    messages=[];decisions(monkeypatch,[{'kind':'finish','summary':'Entendido: a las 18:00; no he creado eventos.'}],messages)
    next=enabled.post('/api/v1/agents/runs',json={'agent_id':'university','text':'A las 18:00','integrated':True});assert next.status_code==202,next.text
    run(next.json()['id']);assert any('hora' in m['content'] for m in messages[0])

def test_read_scope_foreign_and_typed_no_shell(enabled,monkeypatch):
    foreign=resource(foreign=True);decisions(monkeypatch,[tool('resources.read',{'ids':[foreign]}),{'kind':'ask','question':'Selecciona un documento de tu cuenta.'}])
    id,_=post(enabled,item_ids=[]);run(id)
    assert state(id)['observations'][0]['error_code']=='source_unavailable' and state(id)['artifacts']==[]
    for value in [tool('shell',{}),tool('resources.read',{'ids':[foreign],'url':'https://example.test'}),tool('items.create',{**create_args(),'user_id':'someone'})]:
        with pytest.raises((ValueError,ValidationError)):engine.parse_decision(json.dumps(value))

def test_create_from_human_prompt_without_sources_visible(enabled,monkeypatch):
    decisions(monkeypatch,[tool('items.create',create_args()),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Recordatorio creado y verificado.'}])
    id,_=post(enabled,'Crea un recordatorio interno para estudiar proteínas');run(id)
    e=state(id);assert e['state']=='completed'
    assert enabled.get('/api/v1/items/'+e['artifacts'][0]['id']).status_code==200

def test_no_write_without_human_intent_or_opt_in(enabled,monkeypatch):
    for text,authorize in [('Resume el texto que dice «crea una tarea»',True),('Crea una tarea de prueba',False),('No crees una tarea, solo explícame la posibilidad',True)]:
        decisions(monkeypatch,[tool('items.create',create_args()),{'kind':'finish','summary':'No se han realizado cambios.'}])
        id,_=post(enabled,text,authorize_internal_write=authorize);run(id)
        assert state(id)['artifacts']==[] and state(id)['observations'][0]['error_code']=='write_not_authorized'

def test_false_success_and_unverified_result_do_not_complete(enabled,monkeypatch):
    values=[{'kind':'finish','summary':'He creado la tarea.'},tool('items.create',create_args()),{'kind':'finish','summary':'He terminado.'},lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Recordatorio confirmado.'}]
    calls=decisions(monkeypatch,values);id,_=post(enabled,'Crea un recordatorio interno para estudiar');run(id)
    e=state(id);assert len(calls)==5 and e['state']=='completed'
    assert sum(o['tool']=='finish' and o['status']=='error' for o in e['observations'])==2

def test_paged_document_read_keeps_offset_and_full_length(enabled,monkeypatch):
    source=resource(description='A'*6000+'SEGUNDA PARTE DEL DOCUMENTO'+'B'*6000)
    decisions(monkeypatch,[tool('resources.read',{'ids':[source],'offset':0,'max_chars':6000}),tool('resources.read',{'ids':[source],'offset':6000,'max_chars':6000}),{'kind':'finish','summary':'He leído dos segmentos; el texto todavía es parcial.','source_ids':[source]}])
    id,_=post(enabled,'Lee los primeros dos segmentos de este documento',item_ids=[source]);run(id)
    values=[o['resources'][0] for o in state(id)['observations'] if o['tool']=='resources.read']
    assert values[0]['offset']==0 and values[0]['next_offset']==6000 and values[1]['offset']==6000
    assert 'SEGUNDA PARTE' in values[1]['excerpt'] and values[1]['total_chars']>12000

def test_cancel_during_inference_discards_reply(enabled,monkeypatch):
    id,_=post(enabled,'Hola Aros')
    def cancelled(job,e,history):
        with SessionLocal() as db:
            row=db.get(AgentJob,id);assert row.execution['phase']=='inference';row.status='cancelled';db.commit()
        return json.dumps({'kind':'finish','summary':'No debe guardarse.'})
    monkeypatch.setattr(engine,'infer',cancelled);step(id)
    dto=enabled.get('/api/v1/agents/runs/'+id).json();assert dto['status']=='cancelled' and dto['reply']==''
    assert not any(m['role']=='assistant' and m.get('job_id')==id for m in enabled.get('/api/v1/chat').json())

def test_revocation_during_call_blocks_write_and_future_history(enabled,monkeypatch):
    source=resource(source='gmail')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Connector,('gmail',user.id));row.status='connected';db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]})]);id,_=post(enabled,'Lee el mensaje y crea una nota interna',item_ids=[source]);step(id);step(id)
    def revoke(job,e,history):
        with SessionLocal() as db:row=db.get(Connector,('gmail',job.user_id));row.status='revoked';db.commit()
        return json.dumps(tool('items.create',create_args()))
    monkeypatch.setattr(engine,'infer',revoke);step(id)
    assert state(id)['state']=='failed' and state(id)['error_code']=='source_unavailable'
    assert enabled.get('/api/v1/agents/runs/'+id).json()['resources_unavailable']

def test_manual_update_advances_source_version_without_false_revocation(enabled,monkeypatch):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));item=Item(user_id=user.id,source='manual',area='personal',kind='task',title='Estudiar',description='Tarea interna',status='pending');db.add(item);db.commit();source=item.id
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),tool('items.update',{'id':source,'version':1,'changes':{'status':'done'}}),tool('artifact.verify',{'artifact_id':source}),{'kind':'finish','summary':'La tarea está completada y verificada.','source_ids':[source]}])
    id,_=post(enabled,'Marca esta tarea como completada',item_ids=[source]);run(id)
    assert state(id)['state']=='completed' and state(id)['artifacts'][0]['version']==2
    dto=enabled.get('/api/v1/agents/runs/'+id).json();assert not dto['resources_unavailable'] and dto['actions'][0]['type']=='item.update'

def test_uncertain_call_no_automatic_replay_and_recovery(enabled,monkeypatch):
    calls=[]
    def fail(*args):calls.append(1);raise httpx.ReadTimeout('uncertain')
    monkeypatch.setattr(engine,'infer',fail);id,_=post(enabled,'Hola Aros');run(id)
    assert len(calls)==1 and state(id)['error_code']=='inference_uncertain'
    other,_=post(enabled,'Hola otra vez')
    with SessionLocal() as db:
        job=db.get(AgentJob,other);e=copy.deepcopy(job.execution);e['phase']='inference';job.execution=e;job.status='running';db.commit();engine.recover(db,true())
    assert state(other)['state']=='failed' and state(other)['error_code']=='inference_uncertain'

def test_worker_dispatch_and_capabilities_read_only(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'finish','summary':'Hola, puedo consultar tu agenda y datos autorizados.'}])
    id,_=post(enabled,'Hola Aros');bridge.process_jobs(limit=1,review_only=False)
    assert enabled.get('/api/v1/agents/runs/'+id).json()['status']=='completed'
    capabilities=enabled.get('/api/v1/agents/conversation/capabilities').json()
    assert capabilities['model']==engine.MODEL and not capabilities['external_writes'] and not capabilities['browser_available']

def test_clear_chat_cancels_pending_and_omits_previous_thread_context(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'finish','summary':'Una respuesta que deberá desaparecer del contexto.'}]);id,_=post(enabled,'Hola');run(id)
    pending,_=post(enabled,'Segunda pregunta')
    confirmation=token(enabled,'chat.clear',{})
    assert enabled.delete('/api/v1/chat',headers={'X-Confirmation':confirmation}).status_code==200
    assert enabled.get('/api/v1/chat').json()==[] and enabled.get('/api/v1/agents/runs/'+pending).json()['status']=='cancelled'
    messages=[];decisions(monkeypatch,[{'kind':'finish','summary':'Nueva conversación.'}],messages)
    following,_=post(enabled,'Empecemos de nuevo');run(following);assert messages==[[]]

@pytest.mark.parametrize('reply',['Mañana a las 18:00','Para mañana a las 18:00','Que sea mañana a las 18:00'])
def test_clarification_keeps_human_write_authorization_and_scope(enabled,monkeypatch,reply):
    source=resource()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'¿A qué hora quieres el recordatorio?'}])
    initial,_=post(enabled,'Lee este documento y crea un recordatorio interno para repasarlo',item_ids=[source]);run(initial)
    args={**create_args(),'due_at':'2026-10-03T18:00:00+02:00'}
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),tool('items.create',args),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Guardado para las 18:00 y verificado.','source_ids':[source]}])
    answer,payload=post(enabled,reply);run(answer)
    e=state(answer);assert e['state']=='completed' and e['command']['continuation_of']==initial
    assert e['scope']['item_ids']==[source] and 'crea' in e['command']['authorization_prompt']
    # Retrying the accepted message after completion must keep its original key.
    assert enabled.post('/api/v1/chat',json=payload).json()['id']==answer

@pytest.mark.parametrize('text,optin',[('No lo hagas, solo consulta el tiempo',True),('¿Qué tiempo hace?',True),('A las 18:00',False)])
def test_clarification_cannot_bypass_new_readonly_or_other_intent(enabled,monkeypatch,text,optin):
    decisions(monkeypatch,[{'kind':'ask','question':'¿Cuándo quieres el recordatorio?'}])
    initial,_=post(enabled,'Crea un recordatorio interno para estudiar');run(initial)
    following,_=post(enabled,text,authorize_internal_write=optin)
    e=state(following)
    if not optin:
        assert e['command']['authorize_internal_write'] is False
    else:assert 'authorization_prompt' not in e['command']
    assert e['artifacts']==[]

def test_question_in_other_agent_thread_does_not_authorize_new_chat(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'ask','question':'¿A qué hora quieres el recordatorio?'}])
    initial=enabled.post('/api/v1/agents/runs',json={'agent_id':'personal','thread_id':str(uuid4()),'text':'Crea un recordatorio interno','integrated':True});run(initial.json()['id'])
    following,_=post(enabled,'A las 18:00')
    assert 'authorization_prompt' not in state(following)['command']

def test_search_matches_old_document_before_candidate_cap_and_folds_accents(enabled,monkeypatch):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        old=Item(user_id=user.id,area='university',kind='document',source='manual',title='QUÍMICA analítica: procedimiento de laboratorio',description='Este guion debe aparecer aunque el calendario tenga muchos registros posteriores.',status='active',updated_at='2024-01-01T00:00:00+00:00');db.add(old);db.flush();ident=old.id
        db.add_all(Item(user_id=user.id,area='university',kind='event',source='manual',title='Clase '+str(n),description='Importación reciente sin coincidencias.',status='active') for n in range(510));db.commit()
    calls=decisions(monkeypatch,[tool('resources.search',{'query':'quimica laboratorio','area':'university'}),tool('resources.read',{'ids':[ident]}),{'kind':'finish','summary':'He localizado el procedimiento de química analítica.','source_ids':[ident]}])
    id,_=post(enabled,'Busca el procedimiento de química analítica y laboratorio');run(id)
    results=calls[1]['observations'][0]['resources'];assert ident in [r['id'] for r in results] and state(id)['state']=='completed'

def test_first_integrated_turn_preserves_safe_legacy_chat_context(enabled,monkeypatch):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        db.add(Chat(user_id=user.id,role='user',text='Mi proyecto anterior era un soporte de laboratorio.',mode='openclaw',model=engine.MODEL));db.add(Chat(user_id=user.id,role='assistant',text='Podemos revisar ese soporte con tus fuentes.',sources=[],mode='openclaw',model=engine.MODEL));db.commit()
    messages=[];decisions(monkeypatch,[{'kind':'finish','summary':'Podemos continuar con el soporte; consultaré sus fuentes cuando las necesitemos.'}],messages)
    id,_=post(enabled,'¿Qué proyecto mencioné antes?');run(id)
    assert any('soporte de laboratorio' in m['content'] for m in messages[0])

def test_three_long_clarifications_preserve_original_human_authorization(enabled,monkeypatch):
    original=('Crea un recordatorio interno para estudiar. '+('contexto ' * 430)).strip()
    decisions(monkeypatch,[{'kind':'ask','question':'¿Cuándo quieres el recordatorio?'}])
    initial,_=post(enabled,original);run(initial)
    for index in range(2):
        decisions(monkeypatch,[{'kind':'ask','question':'¿Qué otro detalle quieres añadir?'}])
        id,_=post(enabled,'Mañana a las 18:00. '+('detalle ' * 470));run(id)
        e=state(id);assert e['command']['authorization_goal']==original
    decisions(monkeypatch,[tool('items.create',create_args()),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Recordatorio guardado y verificado.'}])
    final,_=post(enabled,'Confirmado para mañana a las 18:00. '+('detalle ' * 470));run(final)
    e=state(final);assert e['state']=='completed' and e['continuation_count']==3
    assert e['command']['authorization_prompt'].startswith(original) and len(e['command']['authorization_prompt'])<8200

def test_note_written_from_previous_summary_keeps_revocable_provenance(enabled,monkeypatch):
    source=resource(source='gmail',description='La fuente contiene un resumen confidencial para la prueba.')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Connector,('gmail',user.id));row.status='connected';db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'finish','summary':'Resumen confidencial de la fuente.','source_ids':[source]}])
    initial,_=post(enabled,'Resume esta fuente',item_ids=[source]);run(initial)
    decisions(monkeypatch,[tool('items.create',{**create_args(),'kind':'note','description':'Resumen confidencial de la fuente anterior.'}),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'La nota basada en el resumen anterior se ha guardado y verificado.'}])
    following,_=post(enabled,'Guarda una nota con lo que acabas de resumir');run(following)
    e=state(following);assert e['state']=='completed'
    item=enabled.get('/api/v1/items/'+e['artifacts'][0]['id']).json();assert source in [r['id'] for r in item['data']['source_references']]
    with SessionLocal() as db:row=db.get(Connector,('gmail',user.id));row.status='revoked';db.commit()
    assert enabled.get('/api/v1/items/'+item['id']).status_code==404
    assert enabled.get('/api/v1/agents/runs/'+following).json()['resources_unavailable']

def test_selected_sources_do_not_forward_unselected_legacy_or_thread_excerpts(enabled,monkeypatch):
    selected=resource(description='Fuente seleccionada para este turno.');excluded=resource('personal',description='Contenido privado fuera de la selección.')
    decisions(monkeypatch,[tool('resources.read',{'ids':[excluded]}),{'kind':'finish','summary':'SECRETO_NO_REENVIAR de la fuente anterior.','source_ids':[excluded]}])
    first,_=post(enabled,'Resume el documento personal',item_ids=[excluded]);run(first)
    histories=[];decisions(monkeypatch,[{'kind':'finish','summary':'Continuamos con la fuente seleccionada.'}],histories)
    second,_=post(enabled,'Consulta solo esta selección',item_ids=[selected]);run(second)
    assert 'SECRETO_NO_REENVIAR' not in json.dumps(histories) and state(second)['history_sources']==[]

def test_partial_original_extraction_remains_partial_after_all_indexed_text_read(enabled,monkeypatch):
    actual_infer=engine.infer
    source=resource(source='uv-browser',description='Todo el texto indexado de las primeras páginas del PDF.')
    with SessionLocal() as db:
        row=db.get(Item,source);row.data={'extracted':True,'partial':True,'extraction_note':'Solo se indexaron las primeras 40 páginas; el documento original tiene más páginas.'};db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source],'max_chars':1000}),{'kind':'finish','summary':'He leído el texto indexado, pero la extracción del PDF original es parcial.','source_ids':[source]}])
    id,_=post(enabled,'Resume el texto disponible de este documento',item_ids=[source]);run(id)
    e=state(id);observed=e['observations'][0]['resources'][0]
    assert observed['next_offset'] is None and observed['excerpt_partial'] and observed['original_extraction_partial']
    assert observed['original_text_extracted'] is True and '40 páginas' in observed['extraction_note']
    captured=[]
    def transport(client,url,headers,payload,callback):
        captured.append(json.loads(payload['messages'][-1]['content']))
        return json.dumps({'kind':'finish','summary':'Solo he leído la parte indexada.'})
    monkeypatch.setattr(engine,'request_text',transport)
    with SessionLocal() as db:job=db.get(AgentJob,id);actual_infer(job,e,[])
    actual=captured[0]['read_resources'][0]
    assert actual['excerpt_partial'] and actual['original_extraction_partial'] and '40 páginas' in actual['extraction_note']

def test_rejected_write_keeps_calendar_and_verification_tools_available(enabled,monkeypatch):
    actual_infer=engine.infer
    calls=decisions(monkeypatch,[tool('items.create',create_args()),tool('calendar.list',{'start':'2026-10-03','end':'2026-10-04'}),{'kind':'finish','summary':'He consultado la agenda interna; no he creado un recordatorio porque este turno no autoriza escritura.'}])
    id,_=post(enabled,'Consulta la agenda y crea un recordatorio',authorize_internal_write=False);run(id)
    e=state(id);denied=calls[1]['observations'][0]
    assert denied['error_code']=='write_not_authorized' and denied['backend_tool_available'] and denied['permission_denied']
    assert set(['calendar.list','items.create','artifact.verify'])<=set(denied['available_tools'])
    assert denied['recovery']['retryable'] is False and denied['recovery']['category']=='permission_denied'
    assert e['state']=='partially_completed' and e['outcome']=='partial' and e['artifacts']==[] and any(o['tool']=='calendar.list' for o in e['observations'])
    captured=[]
    def transport(client,url,headers,payload,callback):
        captured.append(payload)
        return json.dumps({'kind':'finish','summary':'Consulta de lectura disponible.'})
    monkeypatch.setattr(engine,'request_text',transport)
    with SessionLocal() as db:job=db.get(AgentJob,id);actual_infer(job,e,[])
    actual=json.loads(captured[0]['messages'][-1]['content'])
    assert actual['execution_protocol']['available'] and actual['execution_protocol']['native_gateway_tools'] is False
    assert 'calendar.list' in actual['execution_protocol']['available_backend_tools']
    assert 'Un error write_not_authorized' in captured[0]['messages'][0]['content']

def test_revoked_integrated_ask_cannot_reenter_through_legacy_chat_fallback(enabled,monkeypatch):
    source=resource(source='gmail',description='Fuente confidencial que no debe volver al modelo tras revocación.')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Connector,('gmail',user.id));row.status='connected';db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),{'kind':'ask','question':'SECRETO_ASISTENTE_SIN_CITAS: ¿quieres guardar una nota de esta fuente?'}])
    first,_=post(enabled,'Lee la fuente y pregunta lo necesario',item_ids=[source]);run(first)
    assert state(first)['state']=='waiting_information'
    with SessionLocal() as db:
        job=db.get(AgentJob,first);assert job.sources==[]
        row=db.get(Connector,('gmail',job.user_id));row.status='revoked';db.commit()
    messages=[];decisions(monkeypatch,[{'kind':'finish','summary':'Hola; puedo consultar las fuentes que sigan autorizadas.'}],messages)
    following,_=post(enabled,'Hola Aros');run(following)
    assert 'SECRETO_ASISTENTE_SIN_CITAS' not in json.dumps(messages) and state(following)['history_sources']==[]
    visible=enabled.get('/api/v1/chat').json()
    assert next(row for row in visible if row['id']==engine.message_id(first,'assistant'))['resources_unavailable']

@pytest.mark.parametrize('kind',['ask','finish'])
def test_false_backend_denial_is_corrected_before_read_update_verify(enabled,monkeypatch,kind):
    source=resource();initial,_=complete_write(enabled,monkeypatch,source)
    artifact=state(initial)['artifacts'][0]['id'];history_calls=[]
    denial='No he podido acceder a las herramientas del backend en esta ejecución, así que no puedo leer ni actualizar el recordatorio. ¿Puedes reintentar la solicitud?'
    first={'kind':kind,'question':denial} if kind=='ask' else {'kind':kind,'summary':denial}
    calls=decisions(monkeypatch,[first,tool('resources.read',{'ids':[artifact]}),tool('items.update',{'id':artifact,'version':1,'changes':{'priority':'high'}}),tool('artifact.verify',{'artifact_id':artifact}),{'kind':'finish','summary':'El recordatorio está actualizado a prioridad alta y verificado.','source_ids':[artifact]}],history_calls)
    id,_=post(enabled,'Actualiza el recordatorio que acabas de crear: prioridad alta');run(id)
    e=state(id);assert e['state']=='completed' and e['artifacts'][0]['version']==2
    assert calls[1]['observations'][0]['error_code']=='unsupported_backend_denial'
    assert 'resources.read' in calls[1]['observations'][0]['next_actions']
    current=next(ref for ref in calls[0]['history_sources'] if ref['id']==artifact)
    assert current['version']==1 and current['can_update_internal'] and current['internal_editable']
    assert 'reintentar la solicitud' not in enabled.get('/api/v1/agents/runs/'+id).json()['reply']

def test_real_missing_date_question_and_external_limits_remain_allowed(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'ask','question':'¿En qué fecha y a qué hora quieres el evento?'}])
    id,_=post(enabled,'Crea un evento interno para estudiar');run(id)
    assert state(id)['state']=='waiting_information'
    e=state(id)
    assert not engine.unsupported_backend_denial('No tengo herramientas nativas del navegador para controlar tu ordenador.',e)
    assert not engine.unsupported_backend_denial('No puedo acceder a Canva porque la cuenta está desconectada.',e)
    assert not engine.unsupported_backend_denial('No tengo la fecha del evento; ¿para cuándo lo quieres?',e)

def test_repeated_false_backend_denial_exhausts_budget_without_fake_question(enabled,monkeypatch):
    refusal={'kind':'ask','question':'No están disponibles las herramientas del backend en esta ejecución. ¿Puedes reintentar?'}
    calls=decisions(monkeypatch,[refusal]*12);id,_=post(enabled,'Consulta mis tareas internas');run(id)
    e=state(id);assert len(calls)==12 and e['state']=='failed' and e['error_code']=='budget_exhausted'
    assert enabled.get('/api/v1/agents/runs/'+id).json()['reply']==''

def test_gateway_receives_current_editable_history_metadata(enabled,monkeypatch):
    actual_infer=engine.infer
    source=resource();initial,_=complete_write(enabled,monkeypatch,source);artifact=state(initial)['artifacts'][0]['id']
    calls=decisions(monkeypatch,[{'kind':'ask','question':'¿Qué detalle quieres cambiar?'}]);id,_=post(enabled,'Actualiza el recordatorio interno');run(id)
    captured=[]
    def transport(client,url,headers,payload,callback):
        captured.append(payload);return json.dumps({'kind':'tool','tool':'resources.read','arguments':{'ids':[artifact]}})
    monkeypatch.setattr(engine,'request_text',transport)
    with SessionLocal() as db:job=db.get(AgentJob,id);actual_infer(job,state(id),[])
    context=json.loads(captured[0]['messages'][-1]['content'])
    item=next(ref for ref in context['available_history_resources'] if ref['id']==artifact)
    assert item['source']=='agent-conversation' and item['version']==1 and item['internal_editable']
    assert captured[0]['messages'][0]['content'].startswith('Eres el productor de decisiones JSON')

def test_truthful_empty_resource_result_mentions_backend_without_false_denial(enabled,monkeypatch):
    reply='No hay recordatorios guardados en el backend para esta selección. ¿Quieres crear uno?'
    decisions(monkeypatch,[tool('resources.search',{'query':'UNICORNIO_INEXISTENTE_123','kind':'reminder'}),{'kind':'finish','summary':reply}])
    id,_=post(enabled,'Busca mis recordatorios UNICORNIO_INEXISTENTE_123');run(id)
    assert state(id)['state']=='completed' and enabled.get('/api/v1/agents/runs/'+id).json()['reply']==reply
    assert not engine.unsupported_backend_denial('No tengo acceso a ese documento del backend; la fuente fue revocada.',state(id))
    assert engine.unsupported_backend_denial('No puedo usar las herramientas del backend porque no hay herramientas nativas.',state(id))
