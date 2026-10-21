"""Replay context and selected-resource boundaries without calling a provider."""
import copy
import json
from sqlalchemy import select
from app import conversation_engine as engine
from app.database import SessionLocal, Item, AgentJob, User, Connector
from test_api import client, auth
from test_conversation_engine import enabled, post, resource, decisions, tool, run, step, state


def test_first_inference_identifies_exact_selection_without_crediting_read(enabled,monkeypatch):
    source=resource(description='Private body is not metadata')
    actual=engine.infer;captured=[]
    monkeypatch.setattr(engine,'request_text',lambda client,url,headers,payload,callback:
        captured.append(copy.deepcopy(payload)) or json.dumps({'kind':'tool','tool':'resources.read','arguments':{'ids':[source]}}))
    ident,_=post(enabled,'¿Qué dice este documento?',item_ids=[source])
    with SessionLocal() as db:actual(db.get(AgentJob,ident),state(ident),[])
    context=json.loads(captured[0]['messages'][-1]['content'])
    selected=context['selected_resources'][0]
    assert selected['id']==source and selected['title']=='Proteínas y estudio'
    assert selected['source']=='manual' and selected['version']==1 and selected['metadata_only']
    assert 'Private body' not in json.dumps(context)
    assert context['read_resources']==[] and context['observations']==[]
    assert context['execution_protocol']['available_backend_tools']==list(engine.tool_schemas())
    job=enabled.get('/api/v1/agents/runs/'+ident).json()
    assert job['context_sources'][0]=={'id':source,'title':'Proteínas y estudio','area':'learning','kind':'document','source':'manual','version':1,'selected':True}
    assert 'selection_context' not in job['execution']
    assert enabled.get('/api/v1/chat').json()[-1]['context_sources']==job['context_sources']


def test_followup_choice_retains_question_options_and_original_goal(enabled,monkeypatch):
    histories=[]
    decisions(monkeypatch,[{'kind':'ask','question':'¿Prefieres el proveedor A por cobertura o el proveedor B por rapidez?'}],histories)
    first,_=post(enabled,'Recomiéndame un proveedor para el tiempo de Ciudad de ejemplo');run(first)
    decisions(monkeypatch,[{'kind':'finish','summary':'Recomiendo el proveedor A por su cobertura; no he cambiado la configuración.'}],histories)
    following,_=post(enabled,'El que consideres mejor');run(following)
    e=state(following)
    assert e['command']['continuation_of']==first
    assert e['command']['authorization_goal']=='Recomiéndame un proveedor para el tiempo de Ciudad de ejemplo'
    assert any('proveedor A' in message['content'] and 'proveedor B' in message['content'] for message in histories[-1])
    assert any('Ciudad de ejemplo' in message['content'] for message in histories[-1])
    assert e['artifacts']==[]


def test_selection_rejects_foreign_revoked_and_wrong_area_before_enqueue(enabled):
    foreign=resource(foreign=True);wrong_area=resource('events');revoked=resource('personal','gmail')
    for ids,area in [([foreign],'all'),([wrong_area],'learning'),([revoked],'all')]:
        response=enabled.post('/api/v1/chat',json={'text':'Consulta este recurso','integrated':True,'item_ids':ids,'area':area})
        assert response.status_code==404,response.text
    with SessionLocal() as db:
        assert not list(db.scalars(select(AgentJob).where(AgentJob.prompt=='Consulta este recurso')))


def test_selected_source_revoked_before_first_inference_never_reaches_provider(enabled,monkeypatch):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,('gmail',user.id)).status='connected';db.commit()
    source=resource('personal','gmail');calls=decisions(monkeypatch,[{'kind':'finish','summary':'No debe emitirse.'}])
    ident,_=post(enabled,'Consulta este recurso',item_ids=[source])
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,('gmail',user.id)).status='disconnected';db.commit()
    step(ident)
    assert calls==[] and state(ident)['error_code']=='source_unavailable'
    assert enabled.get('/api/v1/agents/runs/'+ident).json()['context_sources']==[]
    assert enabled.get('/api/v1/chat').json()[-1]['context_sources']==[]


def test_selection_metadata_does_not_allow_citing_without_read(enabled,monkeypatch):
    source=resource()
    calls=decisions(monkeypatch,[{'kind':'finish','summary':'Fuente consultada.','source_ids':[source]},
        tool('resources.read',{'ids':[source]}),{'kind':'finish','summary':'Ahora la fuente está leída.','source_ids':[source]}])
    ident,_=post(enabled,'Resume este documento',item_ids=[source]);run(ident)
    assert len(calls)==3 and state(ident)['state']=='completed'
    assert state(ident)['observations'][0]['error_code']=='sources_not_read'
    messages=[message for message in enabled.get('/api/v1/chat').json() if message['job_id']==ident]
    assert all(message['context_sources'][0]['selected'] for message in messages)
    assert messages[-1]['sources'][0]['id']==source


def test_context_switch_preserves_plain_history_but_not_excluded_resource_text(enabled,monkeypatch):
    old=resource(description='Old selected confidential content')
    current=resource('events',description='Current selected content')
    histories=[]
    decisions(monkeypatch,[tool('resources.read',{'ids':[old]}),{'kind':'finish','summary':'Old selected confidential content','source_ids':[old]}])
    first,_=post(enabled,'Resume esta fuente',item_ids=[old]);run(first)
    decisions(monkeypatch,[{'kind':'finish','summary':'Contexto anterior disponible.'}],histories)
    second,_=post(enabled,'Consulta solo este material nuevo',item_ids=[current]);run(second)
    assert all('Old selected confidential content' not in message['content'] for message in histories[-1])
    assert state(second)['selection_context'][0]['id']==current
