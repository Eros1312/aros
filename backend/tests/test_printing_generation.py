"""Local mesh and HTTP integration checks; no provider/GPU/production claims."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import struct
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from app import printing_generation as generation
from app.database import Audit, Base, Connector, Item, Session as LoginSession, User, get_db, now
from app.printing_mesh import MAX_STL_BYTES, OrganizerParameters, generate_organizer, verify_stl
from app.security import csrf, digest
from app.source_access import resource_fingerprint


@pytest.fixture
def db(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'printing-only.sqlite'),connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as session:yield session
    engine.dispose()


@pytest.fixture
def user(db):
    owner=User(email='print-owner@fixture',password_hash='fixture')
    db.add(owner);db.commit();return owner


@pytest.fixture
def client(db,user):
    app=FastAPI(dependencies=[Depends(csrf)]);app.include_router(generation.router)
    def connection():yield db
    app.dependency_overrides[get_db]=connection
    db.add(LoginSession(token_hash=digest('print-token'),user_id=user.id,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()));db.commit()
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer print-token'
        yield client


def enqueue(db,user,**params):
    return generation.queue_generation(db,user,params,str(uuid4()))


@pytest.mark.parametrize('params',[{}, {'columns':1,'rows':1,'width_mm':20,'depth_mm':20,'height_mm':10,'wall_mm':1.2,'base_mm':1.2}, {'columns':6,'rows':6,'width_mm':180,'depth_mm':180,'height_mm':100,'wall_mm':6,'base_mm':8}])
def test_real_binary_mesh_has_closed_edges_expected_volume_and_dimensions(params):
    p=OrganizerParameters(**params);blob,report=generate_organizer(p)
    assert len(blob)==84+report['triangles']*50 and len(blob)<=MAX_STL_BYTES
    assert report['watertight'] and report['vertex_manifold'] and report['components']==1
    assert report['sha256']==hashlib.sha256(blob).hexdigest()
    assert report['dimensions_mm']==[p.width_mm,p.depth_mm,p.height_mm]
    assert report['physical_print_verified'] is False
    # Independent edge reconstruction on bytes consumed by a slicer.
    edges=Counter();volume=0
    for offset in range(84,len(blob),50):
        values=struct.unpack_from('<12fH',blob,offset)
        a,b,c=tuple(values[3:6]),tuple(values[6:9]),tuple(values[9:12])
        for u,v in ((a,b),(b,c),(c,a)):edges[tuple(sorted((u,v)))]+=1
        volume+=(a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    assert set(edges.values())=={2}
    inner_w=(p.width_mm-(p.columns+1)*p.wall_mm)/p.columns
    inner_d=(p.depth_mm-(p.rows+1)*p.wall_mm)/p.rows
    expected=p.width_mm*p.depth_mm*p.height_mm-p.columns*p.rows*inner_w*inner_d*(p.height_mm-p.base_mm)
    assert volume==pytest.approx(expected,rel=1e-5)
    assert generate_organizer(p)[0]==blob


def test_non_round_parameters_remain_manifold_after_float32_serialization():
    random.seed(178)
    for _ in range(30):
        p=OrganizerParameters(width_mm=random.uniform(100,180),depth_mm=random.uniform(100,180),height_mm=random.uniform(20,100),wall_mm=random.uniform(1.2,5),base_mm=random.uniform(1.2,8),columns=random.randint(1,6),rows=random.randint(1,6))
        blob,evidence=generate_organizer(p)
        assert verify_stl(blob,p)==evidence


@pytest.mark.parametrize('params',[{'width_mm':181},{'depth_mm':19},{'height_mm':101},{'columns':7},{'rows':0},{'wall_mm':1.1},{'base_mm':9},{'columns':True},{'width_mm':True},{'width_mm':float('nan')},{'width_mm':float('inf')},{'width_mm':20,'columns':6},{'height_mm':10,'base_mm':8},{'script':'anything'}])
def test_parameters_reject_invalid_and_excessive_inputs(params):
    with pytest.raises(ValidationError):OrganizerParameters(**params)


def test_validator_rejects_corruption_open_mesh_and_wrong_dimensions():
    blob,_=generate_organizer({})
    with pytest.raises(ValueError):verify_stl(blob[:-50],{})
    missing=blob[:80]+struct.pack('<I',struct.unpack_from('<I',blob,80)[0]-1)+blob[84:-50]
    with pytest.raises(ValueError):verify_stl(missing,{})
    corrupt=bytearray(blob);struct.pack_into('<f',corrupt,84+12,float('nan'))
    with pytest.raises(ValueError):verify_stl(bytes(corrupt),{})
    with pytest.raises(ValueError):verify_stl(blob,{'width_mm':121})


def test_acceptance_is_persistent_idempotent_and_commit_false_is_atomic(db,user):
    key=str(uuid4());first=generation.queue_generation(db,user,{},key)
    assert first['state']=='queued' and first['download_url'] is None and first['evidence'] is None
    assert generation.queue_generation(db,user,{},key)['id']==first['id']
    with pytest.raises(HTTPException) as exc:generation.queue_generation(db,user,{'width_mm':140},key)
    assert exc.value.status_code==409
    assert db.scalar(select(func.count()).select_from(Item))==1
    assert db.scalar(select(func.count()).select_from(Audit))==1
    pending=generation.queue_generation(db,user,{},str(uuid4()),commit=False)
    db.rollback()
    assert db.get(Item,pending['id']) is None


def test_queue_limits_and_rate_survive_removing_model(db,user):
    for _ in range(generation.MAX_PENDING):enqueue(db,user)
    with pytest.raises(HTTPException) as exc:enqueue(db,user)
    assert exc.value.status_code==429
    for item in db.scalars(select(Item)):
        db.delete(item)
    for _ in range(generation.MAX_DAILY-generation.MAX_PENDING):db.add(Audit(user_id=user.id,action='printing.generation_queued'))
    db.commit()
    with pytest.raises(HTTPException) as exc:enqueue(db,user)
    assert exc.value.status_code==429


def test_worker_consumes_one_then_download_revalidates_real_blob(db,user,client):
    one=enqueue(db,user);two=enqueue(db,user)
    assert generation.process_generation_batch(db)==one['id']
    assert generation.get_generation(two['id'],user,db)['state']=='queued'
    response=client.get('/api/v1/printing/generation/jobs/'+one['id']+'/file')
    assert response.status_code==200 and response.headers['cache-control']=='private, no-store'
    assert response.content[:4]==b'Aros'
    manifest=client.get('/api/v1/printing/generation/jobs/'+one['id']+'/manifest').json()
    assert hashlib.sha256(response.content).hexdigest()==manifest['evidence']['sha256']
    assert manifest['evidence']['physical_print_verified'] is False
    row=db.get(Item,one['id']);row.blob=row.blob[:-1]+b'\xff';db.commit()
    assert client.get('/api/v1/printing/generation/jobs/'+one['id']+'/file').status_code==409


def test_http_auth_csrf_owner_isolation_and_validation(db,user,client):
    base='/api/v1/printing/generation'
    payload={'parameters':{},'idempotency_key':str(uuid4())}
    assert client.post(base+'/jobs',json=payload).status_code==202
    ident=client.post(base+'/jobs',json=payload).json()['id']
    assert client.post(base+'/jobs',json={'parameters':{'width_mm':500},'idempotency_key':str(uuid4())}).status_code==422
    assert client.get(base+'/jobs/'+ident+'/file').status_code==409
    client.headers.pop('Authorization')
    assert client.get(base+'/jobs').status_code==401
    client.cookies.set('aros_session','print-token')
    assert client.post(base+'/jobs',json=payload).status_code==403
    client.cookies.clear()
    other=User(email='other-print@fixture',password_hash='fixture');db.add(other);db.flush()
    db.add(LoginSession(token_hash=digest('other-token'),user_id=other.id,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()));db.commit()
    client.headers['Authorization']='Bearer other-token'
    assert client.get(base+'/jobs').json()['items']==[]
    for path in ('','/file','/manifest'):assert client.get(base+'/jobs/'+ident+path).status_code==404
    assert client.post(base+'/jobs/'+ident+'/cancel').status_code==404
    assert client.post(base+'/jobs',json=payload).json()['id']!=ident


def test_stale_lease_recovers_once_and_exhaustion_stops(db,user):
    job=enqueue(db,user);row=db.get(Item,job['id'])
    row.status='active';row.data={**row.data,'state':'generating','attempt_count':1,'lease_id':'old','lease_expires_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()};db.commit()
    assert generation.process_generation_batch(db)==job['id']
    db.refresh(row);assert row.data['attempt_count']==2 and row.data['state']=='verified'
    assert generation.process_generation_batch(db) is None
    exhausted=enqueue(db,user);row=db.get(Item,exhausted['id'])
    row.status='active';row.data={**row.data,'state':'generating','attempt_count':3,'lease_expires_at':'invalid'};db.commit()
    assert generation.process_generation_batch(db) is None
    db.refresh(row);assert row.data['state']=='failed' and not row.blob


def test_cancellation_fences_old_worker_completion(db,user,monkeypatch):
    job=enqueue(db,user);original=generation.generate_organizer
    def interrupted(params):
        generation.cancel_generation(job['id'],user,db)
        return original(params)
    monkeypatch.setattr(generation,'generate_organizer',interrupted)
    assert generation.process_generation_batch(db) is None
    db.expire_all();row=db.get(Item,job['id'])
    assert row.data['state']=='cancelled' and row.blob is None


def test_active_lease_not_stolen_and_failures_are_sanitized(db,user,monkeypatch):
    job=enqueue(db,user);row=db.get(Item,job['id']);row.status='active'
    row.data={**row.data,'state':'generating','lease_id':'live','lease_expires_at':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()};db.commit()
    assert generation.process_generation_batch(db) is None
    row.status='pending';db.commit()
    def fail(_):raise RuntimeError('sensitive fixture value')
    monkeypatch.setattr(generation,'generate_organizer',fail)
    assert generation.process_generation_batch(db) is None
    db.refresh(row);assert row.data['state']=='failed' and 'sensitive' not in row.data['error']


def test_sources_revalidated_before_processing_and_downloading(db,user):
    connector=Connector(id='drive',user_id=user.id,name='Drive',status='connected');db.add(connector)
    item=Item(user_id=user.id,kind='document',area='printing',title='Medidas',source='drive',status='active');db.add(item);db.commit()
    ref={'id':item.id,'version':item.version,'content_hash':resource_fingerprint(item)}
    job=generation.queue_generation(db,user,{},str(uuid4()),source_references=[ref])
    assert generation.process_generation_batch(db)==job['id']
    connector.status='revoked';db.commit()
    assert generation.list_generations(user,db)['items']==[]
    with pytest.raises(HTTPException):generation.download_generation(job['id'],user,db)
    connector.status='connected';db.commit()
    second=generation.queue_generation(db,user,{},str(uuid4()),source_references=[ref])
    item.version+=1;db.commit()
    assert generation.process_generation_batch(db) is None
    assert db.get(Item,second['id']).data['state']=='failed'


def test_capabilities_separate_engine_from_worker_liveness(db,user):
    result=generation.capabilities(user,db)
    assert result['available'] is False
    assert result['parametric']['available'] is True and result['parametric']['worker_state']=='unknown'


def test_project_destination_is_owned_available_internal_and_idempotent(db,user):
    project=Item(user_id=user.id,kind='project',area='printing',source='manual',title='Organización',status='active')
    db.add(project);db.commit()
    key=str(uuid4())
    job=generation.queue_generation(db,user,{},key,project_id=project.id)
    assert job['project_id']==project.id
    assert db.get(Item,job['id']).data['project_id']==project.id
    with pytest.raises(HTTPException):generation.queue_generation(db,user,{},key)
    project.area='prisma';db.commit()
    with pytest.raises(HTTPException):generation.queue_generation(db,user,{},str(uuid4()),project_id=project.id)


def test_old_worker_cannot_publish_over_a_new_lease(db,user,monkeypatch):
    job=enqueue(db,user);original=generation.generate_organizer
    def takeover(params):
        row=db.get(Item,job['id']);db.refresh(row)
        db.execute(update(Item).where(Item.id==row.id).values(data={**row.data,'lease_id':'new-owner'},version=row.version+1))
        db.commit()
        return original(params)
    monkeypatch.setattr(generation,'generate_organizer',takeover)
    assert generation.process_generation_batch(db) is None
    db.expire_all();row=db.get(Item,job['id'])
    assert row.data['lease_id']=='new-owner' and row.status=='active' and row.blob is None


def test_cancellation_can_share_conversation_transaction(db,user):
    job=enqueue(db,user)
    assert generation.cancel_job(db,user,job['id'],commit=False)['state']=='cancelled'
    db.rollback();db.expire_all()
    assert generation.get_generation(job['id'],user,db)['state']=='queued'


def test_source_flags_and_withdrawn_records_block_generation(db,user):
    item=Item(user_id=user.id,kind='document',area='printing',title='Dimensiones',source='manual',status='active')
    db.add(item);db.commit()
    ref={'id':item.id,'version':item.version,'content_hash':resource_fingerprint(item)}
    item.data={'available':False};db.commit()
    with pytest.raises(HTTPException):generation.queue_generation(db,user,{},str(uuid4()),source_references=[ref])
    item.data={};item.status='withdrawn';db.commit()
    ref={'id':item.id,'version':item.version,'content_hash':resource_fingerprint(item)}
    with pytest.raises(HTTPException):generation.queue_generation(db,user,{},str(uuid4()),source_references=[ref])
