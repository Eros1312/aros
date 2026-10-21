"""Normal UI edits preserve internal conversation artifacts and dependencies."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_api import client,auth,token
from app.database import SessionLocal,Item,User,Connector
from app.source_access import resource_fingerprint

def seed(kind='note',source='agent-conversation',dependency=False):
    with SessionLocal() as db:
        owner=db.scalar(select(User).where(User.email=='aros@local'))
        refs=[]
        if dependency:
            original=Item(user_id=owner.id,kind='document',area='personal',source='gmail',title='Correo privado',description='Texto de fuente',status='active')
            db.add(original);db.flush()
            refs=[{'id':original.id,'version':original.version,'content_hash':resource_fingerprint(original)}]
            connector=db.get(Connector,('gmail',owner.id));connector.status='connected'
        item=Item(user_id=owner.id,kind=kind,area='personal',source=source,title='Creado en conversación',description='Texto original',status='pending',external_id=str(uuid4()),data={'origin_user_prompt':True,'source_references':refs,'creation_hash':'immutable','execution_id':str(uuid4()),'history':{'kept':True}})
        db.add(item);db.commit();return item.id

def payload(item,**changes):
    return {**{key:item[key] for key in ['kind','area','title','description','status','priority','due_at','data','version']},**changes}

def patch(auth,ident,body):
    headers={}
    if body['kind']=='event':headers={'X-Confirmation':token(auth,'event.update',{'id':ident,**body})}
    return auth.patch('/api/v1/items/'+ident,json=body,headers=headers)

@pytest.mark.parametrize('kind',['note','task','reminder','event','project'])
def test_conversation_artifact_ui_edit_cas_and_archive(auth,kind):
    ident=seed(kind);initial=auth.get('/api/v1/items/'+ident).json()
    body=payload(initial,title='Editado por el usuario',description='Texto editado',status='done',priority='high',due_at='2026-12-01T18:00:00+01:00')
    result=patch(auth,ident,body);assert result.status_code==200,result.text
    edited=result.json();assert edited['version']==2 and edited['data']==initial['data'] and edited['source']=='agent-conversation'
    assert patch(auth,ident,body).status_code==409
    assert patch(auth,ident,payload(edited,status='archived')).status_code==200

@pytest.mark.parametrize('change',[{'data':{}},{'data':{'source_references':[]}},{'data':{'creation_hash':'forged'}},{'data':{'execution_id':'forged'}},{'data':{'history':{}}},{'data':{'location':'new'}},{'area':'learning'},{'kind':'task'}])
def test_conversation_metadata_and_scope_cannot_be_removed_or_forged(auth,change):
    ident=seed();initial=auth.get('/api/v1/items/'+ident).json()
    assert auth.patch('/api/v1/items/'+ident,json=payload(initial,**change)).status_code==422
    assert auth.get('/api/v1/items/'+ident).json()==initial

def test_omitted_metadata_preserved_and_revoked_dependency_still_hidden(auth):
    ident=seed(dependency=True);initial=auth.get('/api/v1/items/'+ident).json()
    body=payload(initial,title='Edición conservando procedencia');body.pop('data')
    result=auth.patch('/api/v1/items/'+ident,json=body);assert result.status_code==200,result.text
    assert result.json()['data']==initial['data']
    with SessionLocal() as db:
        owner=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,('gmail',owner.id)).status='revoked';db.commit()
    assert auth.get('/api/v1/items/'+ident).status_code==404
    assert auth.patch('/api/v1/items/'+ident,json=payload(result.json(),title='No autorizado')).status_code==404
    assert ident not in {item['id'] for item in auth.get('/api/v1/items').json()}

def test_all_day_date_edit_rejected_instead_of_leaving_old_interval(auth):
    ident=seed('event')
    with SessionLocal() as db:
        item=db.get(Item,ident);item.due_at='2026-10-25T00:00:00.000000+00:00';item.data.update(all_day=True,start_date='2026-10-25',end_at='2026-10-26')
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(item,'data');db.commit()
    initial=auth.get('/api/v1/items/'+ident).json()
    result=patch(auth,ident,payload(initial,due_at='2026-10-26T00:00:00+00:00'))
    assert result.status_code==422 and 'intervalo' in result.text
    assert auth.get('/api/v1/items/'+ident).json()==initial

@pytest.mark.parametrize('source',['learning','learning-browser','events-file','canva'])
def test_imported_originals_remain_uneditable(auth,source):
    ident=seed(source=source);initial=auth.get('/api/v1/items/'+ident)
    # REST-connected originals may be hidden until the owner grants access.
    if initial.status_code==404:
        with SessionLocal() as db:
            owner=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,(source,owner.id)).status='connected';db.commit()
        initial=auth.get('/api/v1/items/'+ident)
    assert initial.status_code==200
    assert auth.patch('/api/v1/items/'+ident,json=payload(initial.json(),title='Alterado')).status_code==409
