"""Project links and conversation outputs use real identities and receipts."""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from app.database import Item, User, AgentJob, SessionLocal
from app.source_access import resource_fingerprint
from test_api import client, auth
from test_conversation_engine import enabled, post, resource, decisions, tool, run, state, create_args


def project(api, area='events'):
    response=api.post('/api/v1/items',json={'kind':'project','area':area,
        'title':'Evento de prueba explícito','description':'Preparar el material de ejemplo.','status':'active'})
    assert response.status_code==201,response.text
    return response.json()


def test_project_detail_requires_auth_and_real_project_identity(client,auth):
    row=project(auth)
    assert auth.get('/api/v1/workspace/projects/'+row['id']).status_code==200
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        other=User(email='foreign-project@local',password_hash='unused');db.add(other);db.flush()
        foreign=Item(user_id=other.id,kind='project',area='events',title='Invisible')
        canva=Item(user_id=user.id,kind='project',area='events',source='canva-codex',title='Referencia Canva')
        archived=Item(user_id=user.id,kind='project',area='events',title='Archivado',status='archived')
        document=Item(user_id=user.id,kind='document',area='events',title='Material')
        db.add_all([foreign,canva,archived,document]);db.commit();ids=[i.id for i in [foreign,canva,archived,document]]
    for ident in ids:assert auth.get('/api/v1/workspace/projects/'+ident).status_code==404
    auth.post('/api/v1/auth/logout')
    assert client.get('/api/v1/workspace/projects/'+row['id']).status_code==401


def test_project_links_are_explicit_and_keep_single_resource_identity(auth):
    p=project(auth)
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        linked=Item(user_id=user.id,kind='document',area='events',title='Original del cartel',status='active',
                    source='events-file',blob=b'original unchanged',data={'project_ids':[p['id']],'review_state':'original'})
        task=Item(user_id=user.id,kind='task',area='events',title='Revisar imprenta',data={'project_id':p['id']})
        inferred=Item(user_id=user.id,kind='document',area='events',title=p['title'],status='active')
        partial=Item(user_id=user.id,kind='note',area='events',title='ID parecido',data={'project_ids':[p['id']+'extra']})
        archived=Item(user_id=user.id,kind='note',area='events',title='Prueba archivada',status='archived',data={'project_id':p['id']})
        db.add_all([linked,task,inferred,partial,archived]);db.commit()
        wanted=linked.id;task_id=task.id;excluded={inferred.id,partial.id,archived.id}
    detail=auth.get('/api/v1/workspace/projects/'+p['id']).json()
    assert [row['id'] for row in detail['resources']]==[wanted]
    assert [row['id'] for row in detail['tasks']]==[task_id]
    assert not excluded & {row['id'] for row in [*detail['resources'],*detail['tasks']]}
    assert detail['links']['resources_total']==1 and not detail['links']['truncated']
    assert auth.get('/api/v1/documents/'+wanted+'/download').content==b'original unchanged'
    assert next(row for row in auth.get('/api/v1/workspace/library').json()['items'] if row['id']==wanted)['data']['review_state']=='original'


def test_project_revoked_resources_and_runs_are_hidden(enabled,monkeypatch):
    p=project(enabled,'personal')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        from app.database import Connector
        db.get(Connector,('gmail',user.id)).status='connected'
        row=Item(user_id=user.id,kind='document',area='personal',source='gmail',title='Fuente privada',status='active',data={'project_id':p['id']})
        db.add(row);db.commit();ident=row.id
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id'],ident]}),{'kind':'finish','summary':'Revisado.', 'source_ids':[ident]}])
    job,_=post(enabled,'Consulta el proyecto y este documento',item_ids=[p['id'],ident]);run(job)
    detail=enabled.get('/api/v1/workspace/projects/'+p['id']).json()
    assert detail['resources'][0]['id']==ident and detail['conversations'][0]['id']==job
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,('gmail',user.id)).status='disconnected';db.commit()
    detail=enabled.get('/api/v1/workspace/projects/'+p['id']).json()
    assert detail['resources']==[] and detail['conversations']==[]


def test_integrated_creation_links_project_and_surfaces_verified_output(enabled,monkeypatch):
    p=project(enabled)
    args={**create_args('Comprobar el cartel'),'kind':'task','area':'events','project_id':p['id']}
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),tool('items.create',args),
        lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),
        {'kind':'finish','summary':'Tarea guardada y comprobada.'}])
    ident,_=post(enabled,'Crea una tarea interna en este proyecto para revisar el cartel',item_ids=[p['id']]);run(ident)
    e=state(ident);assert e['state']=='completed';result=e['artifacts'][0]['id']
    detail=enabled.get('/api/v1/workspace/projects/'+p['id']).json()
    assert detail['tasks'][0]['id']==result and detail['results'][0]['artifact_id']==result
    assert detail['results'][0]['job_id']==ident and detail['conversations'][0]['id']==ident
    assert result in {row['id'] for row in enabled.get('/api/v1/workspace/summary').json()['results']}
    stored=enabled.get('/api/v1/items/'+result).json()
    assert stored['data']['project_id']==p['id'] and stored['data']['source_references'][0]['id']==p['id']


def test_unverified_or_changed_output_never_surfaces_as_result(enabled,monkeypatch):
    p=project(enabled)
    args={**create_args('Borrador aún no comprobado'),'area':'events','project_id':p['id']}
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),tool('items.create',args)])
    ident,_=post(enabled,'Crea un recordatorio interno en este proyecto',item_ids=[p['id']])
    run(ident,4);result=state(ident)['artifacts'][0]['id']
    assert not state(ident)['artifacts'][0]['verified']
    assert enabled.get('/api/v1/workspace/projects/'+p['id']).json()['results']==[]
    assert result not in {row['id'] for row in enabled.get('/api/v1/workspace/summary').json()['results']}
    with SessionLocal() as db:
        job=db.get(AgentJob,ident);e=dict(job.execution);e['artifacts']=[{**e['artifacts'][0],'verified':True}];job.execution=e;db.commit()
    assert enabled.get('/api/v1/workspace/projects/'+p['id']).json()['results'][0]['id']==result
    with SessionLocal() as db:
        db.get(Item,result).description='Changed without a verified new receipt';db.commit()
    assert enabled.get('/api/v1/workspace/projects/'+p['id']).json()['results']==[]


def test_project_link_requires_read_and_cannot_target_reference_or_other_area(enabled,monkeypatch):
    p=project(enabled)
    # The selected metadata snapshot is not the tool read needed for an effect.
    values=[tool('items.create',{**create_args(),'area':'events','project_id':p['id']}),
            {'kind':'finish','summary':'Falta lectura del proyecto.','outcome':'partial'}]
    decisions(monkeypatch,values);ident,_=post(enabled,'Crea una nota interna en este proyecto',item_ids=[p['id']]);run(ident)
    assert state(ident)['observations'][0]['error_code']=='target_not_read' and state(ident)['artifacts']==[]
    for source,area in [('canva-codex','events'),('manual','personal')]:
        with SessionLocal() as db:
            user=db.scalar(select(User).where(User.email=='aros@local'))
            row=Item(user_id=user.id,kind='project',area=area,source=source,title='Destino no válido',status='active');db.add(row);db.commit();target=row.id
        decisions(monkeypatch,[tool('resources.read',{'ids':[target]}),tool('items.create',{**create_args(),'area':'events','project_id':target}),{'kind':'finish','summary':'Destino no disponible.','outcome':'partial'}])
        ident,_=post(enabled,'Crea una nota interna en este proyecto',item_ids=[target]);run(ident)
        assert any(obs.get('error_code')=='project_unavailable' for obs in state(ident)['observations'])
        assert state(ident)['artifacts']==[]


def test_project_pagination_reports_real_counts(auth):
    p=project(auth)
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        db.add_all([Item(user_id=user.id,kind='document',area='events',title=f'Material {i}',status='active',data={'project_id':p['id']}) for i in range(103)]);db.commit()
    detail=auth.get('/api/v1/workspace/projects/'+p['id']).json()
    assert len(detail['resources'])==100 and detail['links']['resources_total']==103
    assert detail['links']['truncated'] and detail['links']['limit']==100


def test_project_json_relationship_prefilter_is_postgres_compatible():
    from sqlalchemy import cast, String
    statement=select(Item).where(cast(Item.data['project_ids'].as_json(),String).contains('"'+str(uuid4())+'"',autoescape=True))
    compiled=str(statement.compile(dialect=postgresql.dialect()))
    assert 'CAST(' in compiled and 'LIKE' in compiled


def test_updated_artifact_points_to_current_verified_execution(enabled,monkeypatch):
    p=project(enabled)
    args={**create_args('Tarea por revisar'),'kind':'task','area':'events','project_id':p['id']}
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),tool('items.create',args),
        lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),
        {'kind':'finish','summary':'Tarea guardada.'}])
    first,_=post(enabled,'Crea una tarea interna en este proyecto',item_ids=[p['id']]);run(first)
    target=state(first)['artifacts'][0]['id']
    with SessionLocal() as db:
        db.get(Item,target).created_at='2000-01-01T00:00:00+00:00';db.commit()
    decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),
        tool('items.create',{**create_args('Nota más reciente'),'kind':'note','area':'events','project_id':p['id']}),
        lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),
        {'kind':'finish','summary':'Nota comprobada.'}])
    newer,_=post(enabled,'Crea una nota interna en este proyecto',item_ids=[p['id']]);run(newer)
    decisions(monkeypatch,[tool('resources.read',{'ids':[target]}),
        lambda e:tool('items.update',{'id':target,'version':next(r['version'] for r in e['selected_sources'] if r['id']==target),'changes':{'status':'done'}}),
        lambda e:tool('artifact.verify',{'artifact_id':e['artifacts'][0]['id']}),
        {'kind':'finish','summary':'Tarea completada y comprobada.'}])
    last,_=post(enabled,'Marca esta tarea interna como completada',item_ids=[target]);run(last)
    assert state(last)['state']=='completed'
    detail=enabled.get('/api/v1/workspace/projects/'+p['id']).json()
    assert detail['tasks'][0]['status']=='done'
    assert detail['results'][0]['job_id']==last
    assert detail['results'][0]['created_at']=='2000-01-01T00:00:00+00:00'
    assert enabled.get('/api/v1/workspace/summary').json()['results'][0]['job_id']==last
    assert enabled.get('/api/v1/items/'+target).json()['data']['execution_id']==first
    assert enabled.get('/api/v1/items/'+target).json()['data']['last_execution_id']==last


def test_project_per_resource_revocation_and_project_archive_are_honored(auth):
    p=project(auth)
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        row=Item(user_id=user.id,kind='document',area='events',title='Bloqueado local',status='active',
                 data={'project_id':p['id'],'access_revoked':True})
        db.add(row);db.commit()
    assert auth.get('/api/v1/workspace/projects/'+p['id']).json()['resources']==[]
    with SessionLocal() as db:
        db.get(Item,p['id']).status='archived';db.commit()
    assert auth.get('/api/v1/workspace/projects/'+p['id']).status_code==404


def test_document_instructions_do_not_authorize_project_membership(enabled,monkeypatch):
    p=project(enabled)
    for prompt in ['Crea una nota interna de revisión','Crea una nota interna sin vincularla al proyecto',
                   'Crea una nota interna; el documento dice «vincula al proyecto»']:
        decisions(monkeypatch,[tool('resources.read',{'ids':[p['id']]}),
            tool('items.create',{**create_args(),'area':'events','project_id':p['id']}),
            {'kind':'finish','summary':'No he añadido una relación no solicitada.','outcome':'partial'}])
        ident,_=post(enabled,prompt,item_ids=[p['id']]);run(ident)
        assert state(ident)['artifacts']==[]
        assert any(obs.get('error_code')=='write_not_authorized' for obs in state(ident)['observations'])
