"""Context lineage, project isolation and truthful durable effects; no provider I/O."""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from app import conversation_engine as engine, conversation_tools as tools
from app.database import AgentJob, Connector, Item, SessionLocal, User
from app.source_access import resource_fingerprint, visible_items_clause
from app.agent_capabilities import CapabilityBlocked
from test_api import client, auth
from test_conversation_engine import enabled, post, resource, decisions, tool, run, step, state, create_args
from test_workspace_projects import project


@pytest.mark.parametrize('context_tool',['resources.search','calendar.list'])
def test_every_exposed_context_becomes_revocable_lineage(enabled,monkeypatch,context_tool):
    source=resource('personal',description='Private navigation passage')
    with SessionLocal() as db:
        row=db.get(Item,source)
        if context_tool=='calendar.list':
            row.kind='event';row.due_at='2050-01-01T10:00:00+00:00'
        db.commit()
    query=tool('resources.search',{'query':'Private navigation'}) if context_tool=='resources.search' else tool('calendar.list',{'start':'2050-01-01','end':'2050-01-02'})
    decisions(monkeypatch,[query,tool('items.create',create_args()),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Nota guardada con la procedencia observada.'}])
    ident,_=post(enabled,'Crea una nota interna con lo que encuentres',item_ids=[source]);run(ident)
    e=state(ident);assert e['state']=='completed'
    artifact=e['artifacts'][0]['id']
    with SessionLocal() as db:
        assert [ref['id'] for ref in db.get(Item,artifact).data['source_references']]==[source]
        row=db.get(Item,source);row.data={**row.data,'access_revoked':True};db.commit()
    assert enabled.get('/api/v1/items/'+source).status_code==404
    assert enabled.get('/api/v1/items/'+artifact).status_code==404
    assert enabled.get('/api/v1/agents/runs/'+ident).json()['resources_unavailable']


@pytest.mark.parametrize('revocation',[{'revoked':True},{'access_revoked':True},{'available':False},'calendar','withdrawn','canceled'])
def test_direct_download_and_transitive_derived_access_follow_revocation(enabled,revocation):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        if revocation=='calendar':db.get(Connector,('calendar',user.id)).status='connected'
        source=Item(user_id=user.id,kind='document',area='learning',title='Original',description='Private body',source='calendar' if revocation=='calendar' else 'manual',blob=b'private',status='active')
        db.add(source);db.flush()
        parent=source
        for index in range(2):
            child=Item(user_id=user.id,kind='note',area='learning',title=f'Derived {index}',description='Private body',source='agent-conversation',status='active',data={'origin_user_prompt':True,'source_references':[{'id':parent.id,'version':parent.version,'content_hash':resource_fingerprint(parent)}]})
            db.add(child);db.flush();parent=child
        ident=source.id;derived=parent.id;db.commit()
        assert db.scalar(select(Item.id).where(Item.id==derived,visible_items_clause(db,user)))==derived
        if isinstance(revocation,dict):source.data=revocation
        elif revocation=='calendar':
            connector=db.get(Connector,('calendar',user.id))
            if connector:connector.status='revoked'
            else:db.add(Connector(id='calendar',user_id=user.id,name='Calendario',status='revoked'))
        else:source.status=revocation
        db.commit()
    assert enabled.get('/api/v1/items/'+derived).status_code==404
    if isinstance(revocation,dict) or revocation=='calendar':
        assert enabled.get('/api/v1/documents/'+ident+'/download').status_code==404


def test_lineage_cycles_and_malformed_references_are_hidden(enabled):
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        a=Item(user_id=user.id,kind='note',area='personal',title='A',source='agent-conversation')
        b=Item(user_id=user.id,kind='note',area='personal',title='B',source='agent-conversation')
        db.add_all([a,b]);db.flush()
        a.data={'origin_user_prompt':True,'source_references':[{'id':b.id,'version':1}]}
        b.data={'origin_user_prompt':True,'source_references':[{'id':a.id,'version':1}]}
        bad=Item(user_id=user.id,kind='note',area='personal',title='Malformed',source='agent-conversation',data={'origin_user_prompt':True,'source_references':{'id':'wrong'}})
        db.add(bad);db.commit();ids=[a.id,b.id,bad.id]
    for ident in ids:assert enabled.get('/api/v1/items/'+ident).status_code==404


def test_project_threads_and_plain_history_are_isolated(enabled,monkeypatch):
    a=project(enabled);b=project(enabled)
    decisions(monkeypatch,[{'kind':'finish','summary':'Private project A reply.'}])
    first,_=post(enabled,'Private project A plan.',project_id=a['id'],thread_id=a['id']);run(first)
    histories=[];decisions(monkeypatch,[{'kind':'finish','summary':'Project B only.'}],histories)
    second,_=post(enabled,'Another project',project_id=b['id'],thread_id=b['id']);run(second)
    assert histories==[[]]
    assert state(second)['scope']=={'area':'events','areas':['events'],'item_ids':[b['id']],'project_id':b['id']}
    assert all(message['job_id']==second for message in enabled.get('/api/v1/chat',params={'project_id':b['id'],'thread_id':b['id']}).json())
    assert enabled.get('/api/v1/chat').json()==[]
    assert enabled.post('/api/v1/chat',json={'text':'Wrong thread','integrated':True,'project_id':a['id'],'thread_id':b['id']}).status_code==422


def test_project_upload_selects_exact_material_and_rejects_unlinked_foreign_scope(enabled):
    p=project(enabled)
    uploaded=enabled.post('/api/v1/documents/upload',data={'area':'events','project_id':p['id']},files={'file':('brief.txt',b'Actual brief','text/plain')})
    assert uploaded.status_code==201,uploaded.text
    material=uploaded.json();assert material['data']['project_id']==p['id']
    ident,_=post(enabled,'Consulta este material',project_id=p['id'],item_ids=[material['id']])
    assert set(state(ident)['scope']['item_ids'])=={p['id'],material['id']}
    enabled.post('/api/v1/agents/runs/'+ident+'/cancel')
    unrelated=resource('events')
    assert enabled.post('/api/v1/chat',json={'text':'Consulta','integrated':True,'project_id':p['id'],'item_ids':[unrelated]}).status_code==404
    assert enabled.post('/api/v1/documents/upload',data={'area':'personal','project_id':p['id']},files={'file':('x.txt',b'x','text/plain')}).status_code==422


def test_project_membership_change_stops_before_provider(enabled,monkeypatch):
    p=project(enabled)
    upload=enabled.post('/api/v1/documents/upload',data={'area':'events','project_id':p['id']},files={'file':('brief.txt',b'Actual brief','text/plain')}).json()
    ident,_=post(enabled,'Consulta este material',project_id=p['id'],item_ids=[upload['id']])
    with SessionLocal() as db:db.get(Item,upload['id']).data={};db.commit()
    calls=decisions(monkeypatch,[{'kind':'finish','summary':'Must not run'}]);step(ident)
    assert calls==[] and state(ident)['error_code']=='scope_denied'


def test_cancel_after_effect_preserves_truthful_receipt(enabled,monkeypatch):
    decisions(monkeypatch,[tool('items.create',create_args())])
    ident,_=post(enabled,'Crea un recordatorio interno');run(ident,2)
    artifact=state(ident)['artifacts'][0]['id']
    cancelled=enabled.post('/api/v1/agents/runs/'+ident+'/cancel').json()
    assert cancelled['status']=='cancelled' and cancelled['execution']['artifacts'][0]['id']==artifact
    assert 'conservan' in cancelled['execution']['recovery_hint']
    assert enabled.get('/api/v1/items/'+artifact).status_code==200


def test_partial_is_terminal_and_visible_without_claiming_completed_execution(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'finish','summary':'Esta parte queda pendiente.','outcome':'partial'}])
    ident,_=post(enabled,'Explica qué falta');run(ident)
    job=enabled.get('/api/v1/agents/runs/'+ident).json()
    assert job['status']=='completed' and job['reply']=='Esta parte queda pendiente.'
    assert job['execution']['state']=='partially_completed' and job['execution']['outcome']=='partial'


def test_noop_keeps_version_and_verification_proves_effect_fields(enabled,monkeypatch):
    source=resource('personal')
    with SessionLocal() as db:row=db.get(Item,source);row.kind='task';row.status='done';db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[source]}),tool('items.update',{'id':source,'version':1,'changes':{'status':'done'}}),tool('artifact.verify',{'artifact_id':source}),{'kind':'finish','summary':'Ya estaba completada; no hubo cambios.'}])
    ident,_=post(enabled,'Marca esta tarea como completada',item_ids=[source]);run(ident)
    e=state(ident);assert e['artifacts'][0]['effect']=='unchanged' and e['artifacts'][0]['verified']
    with SessionLocal() as db:
        row=db.get(Item,source);assert row.version==1
        row.due_at='2050-01-01T10:00:00+00:00';db.commit()
        user=db.scalar(select(User).where(User.email=='aros@local'))
        with pytest.raises(CapabilityBlocked,match='no coincide'):engine.verify_artifact(db,user,e,source)


def test_selected_metadata_alone_does_not_credit_project_read(enabled,monkeypatch):
    p=project(enabled)
    decisions(monkeypatch,[tool('items.create',{**create_args(),'area':'events','project_id':p['id']}),{'kind':'finish','summary':'Es necesaria la lectura.','outcome':'partial'}])
    ident,_=post(enabled,'Crea una tarea en este proyecto',project_id=p['id']);run(ident)
    assert state(ident)['observations'][0]['error_code']=='target_not_read'
    assert not state(ident)['artifacts']


def test_verified_project_result_is_inherited_for_exact_followup(enabled,monkeypatch):
    p=project(enabled)
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),tool('items.create',{**create_args(),'area':'events','project_id':p['id'],'kind':'note'}),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Nota guardada.'}])
    first,_=post(enabled,'Crea una nota en este proyecto',project_id=p['id']);run(first)
    result=state(first)['artifacts'][0]['id']
    decisions(monkeypatch,[tool('resources.read',{'ids':[result]}),tool('items.update',{'id':result,'version':1,'changes':{'title':'Título corregido'}}),tool('artifact.verify',{'artifact_id':result}),{'kind':'finish','summary':'Nota actualizada y comprobada.'}])
    followup,_=post(enabled,'Actualiza el título de la nota anterior a Título corregido',project_id=p['id']);run(followup)
    assert state(followup)['state']=='completed'
    job=enabled.get('/api/v1/agents/runs/'+followup).json()
    inherited=next(ref for ref in job['context_sources'] if ref['id']==result)
    assert inherited['inherited'] and not inherited['selected']
    assert enabled.get('/api/v1/items/'+result).json()['version']==2


def test_result_based_on_unselected_material_is_not_inherited(enabled,monkeypatch):
    p=project(enabled)
    uploaded=enabled.post('/api/v1/documents/upload',data={'area':'events','project_id':p['id']},files={'file':('private.txt',b'Private material','text/plain')}).json()
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id'],uploaded['id']]}),tool('items.create',{**create_args(),'area':'events','project_id':p['id']}),lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),{'kind':'finish','summary':'Derivado privado guardado.'}])
    first,_=post(enabled,'Crea una nota en este proyecto',project_id=p['id'],item_ids=[uploaded['id']]);run(first)
    artifact=state(first)['artifacts'][0]['id']
    followup,_=post(enabled,'Consulta solo la ficha del proyecto',project_id=p['id'])
    assert artifact not in state(followup)['scope']['item_ids']
    assert uploaded['id'] not in state(followup)['scope']['item_ids']


def test_runtime_last_completed_excludes_partial_and_waiting_information(enabled,monkeypatch):
    decisions(monkeypatch,[{'kind':'finish','summary':'Pendiente.','outcome':'partial'}])
    ident,_=post(enabled,'Qué falta');run(ident)
    runtime=enabled.get('/api/v1/agents/runtime').json()
    assert next(agent for agent in runtime['agents'] if agent['agent_id']=='aros')['last_completed_at'] is None


def test_numeric_creation_request_requires_each_effect():
    from app.conversation_objectives import minimum_creation_objectives
    assert minimum_creation_objectives('Crea dos tareas internas')==2
    assert minimum_creation_objectives('Crea 3 recordatorios')==3
    assert minimum_creation_objectives('No crees dos tareas')==0


def test_archived_copy_cannot_bypass_revoked_lineage(enabled):
    source=resource('personal')
    with SessionLocal() as db:
        original=db.get(Item,source)
        copied=Item(user_id=original.user_id,area='personal',kind='note',source='agent-conversation',title='Archived private copy',description='Private body',status='archived',data={'origin_user_prompt':True,'source_references':[{'id':source,'version':original.version,'content_hash':resource_fingerprint(original)}]})
        db.add(copied);db.commit();ident=copied.id
        original.data={'revoked':True};db.commit()
    assert enabled.get('/api/v1/items/'+ident).status_code==404








def test_effect_and_acceptance_receipt_share_real_sqlite_transaction(enabled,monkeypatch):
    decisions(monkeypatch,[tool('items.create',create_args())])
    ident,_=post(enabled,'Crea un recordatorio interno');step(ident)
    original_save=engine.save
    def reject_effect_commit(db,job,lease,e,*args,**kwargs):
        if e.get('artifacts'):db.rollback();return False
        return original_save(db,job,lease,e,*args,**kwargs)
    monkeypatch.setattr(engine,'save',reject_effect_commit);step(ident)
    with SessionLocal() as db:
        assert not list(db.scalars(select(Item.id).where(Item.source=='agent-conversation',Item.external_id.startswith(ident+':'))))


def test_real_usage_replaces_provisional_report_for_same_decision(enabled,monkeypatch):
    import json
    actual=engine.infer
    def measured(client,url,headers,payload,progress):
        progress('check','')
        progress('usage',{'prompt_tokens':3,'completion_tokens':1,'total_tokens':4,'reported':True})
        progress('usage',{'model':'openclaw/aros-planner','prompt_tokens':3,'completion_tokens':2,'total_tokens':5,'reported':True})
        return json.dumps({'kind':'finish','summary':'Respuesta de prueba.'})
    monkeypatch.setattr(engine,'request_text',measured)
    ident,_=post(enabled,'Hola');run(ident)
    usage=state(ident)['usage']
    assert len(usage['calls'])==1 and usage['calls'][0]['total_tokens']==5
    assert usage['calls'][0]['reported_by']=='gateway' and usage['cost'] is None
