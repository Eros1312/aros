"""Generic workspace contracts and lossless legacy area migration."""
from sqlalchemy import select
from app.database import Base, Item, User, AgentJob, AgentSchedule, Connector, Automation
from app.workspace_migration import generalize_workspace
from app.source_access import resource_fingerprint, visible_items_clause
from test_api import client, auth


def test_generic_resources_links_and_removed_specialized_routes(auth):
    for area in ['learning','events','resources']:
        response=auth.post('/api/v1/items',json={'kind':'link','area':area,'title':'Referencia de ejemplo','data':{'url':'https://example.com/reference'}})
        assert response.status_code==201,response.text
        assert auth.get('/api/v1/items/'+response.json()['id']).json()['data']['url']=='https://example.com/reference'
    assert auth.post('/api/v1/items',json={'kind':'link','area':'resources','title':'Referencia insegura','data':{'url':'javascript:alert(1)'}}).status_code==422
    for path in ['/api/v1/printing/catalog','/api/v1/printing/generation','/api/v1/university/status','/api/v1/university-calendar','/api/v1/prisma/library']:
        assert auth.get(path).status_code==404
    assert auth.post('/api/v1/documents/upload',data={'area':'resources'},files={'file':('removed.stl',b'solid x','application/octet-stream')}).status_code==422


def test_transition_preserves_files_owner_and_valid_lineage_and_cancels_old_work(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'));Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(email='migration@example.test',password_hash='test');db.add(user);db.flush()
        original=Item(user_id=user.id,kind='document',area='university',source='university-browser',title='My original',blob=b'unchanged',data={});db.add(original);db.flush()
        copied=Item(user_id=user.id,kind='note',area='prisma',source='agent-conversation',title='My note',data={'source_references':[{'id':original.id,'version':original.version,'content_hash':resource_fingerprint(original)}]})
        legacy=Item(user_id=user.id,kind='model',area='printing',source='manual',title='My legacy file',blob=b'legacy')
        remote=Item(user_id=user.id,kind='document',area='university',source='university',title='Revoked remote',data={})
        job=AgentJob(user_id=user.id,agent_id='printing',prompt='Legacy task',execution={'scope':{'areas':['printing']}})
        db.add_all([copied,legacy,remote,job,Connector(id='university',user_id=user.id,name='Retired',status='revoked',encrypted_tokens='retired-secret'),AgentSchedule(user_id=user.id,agent_id='prisma',enabled=True,thread_id='main'),Automation(user_id=user.id,kind='printing_discovery',title='Legacy discovery',enabled=True)])
        db.flush();ids=[original.id,copied.id,legacy.id,remote.id,job.id,user.id];db.commit()
    for _ in range(2):
        with engine.begin() as connection:generalize_workspace(connection)
    with Session(engine) as db:
        original,copied,legacy,remote=[db.get(Item,i) for i in ids[:4]];user=db.get(User,ids[-1])
        assert (original.area,original.source,original.blob)==('learning','upload',b'unchanged')
        assert (legacy.area,legacy.kind,legacy.blob)==('resources','document',b'legacy')
        assert copied.area=='events' and copied.data['source_references'][0]['content_hash']==resource_fingerprint(original)
        assert db.scalar(select(Item.id).where(Item.id==copied.id,visible_items_clause(db,user)))==copied.id
        assert not db.scalar(select(Item.id).where(Item.id==remote.id,visible_items_clause(db,user)))
        assert db.get(AgentJob,ids[4]).status=='cancelled'
        assert not db.get(AgentSchedule,(user.id,'events')).enabled
        assert not db.scalar(select(Automation)).enabled
        assert not db.get(Connector,('university',user.id))
    engine.dispose()


def test_transition_handles_only_a_legacy_schedule_without_cancelling_later_jobs(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine=create_engine('sqlite:///'+str(tmp_path/'schedule.db'));Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(email='schedule@example.test',password_hash='test');db.add(user);db.flush();user_id=user.id
        db.add(AgentSchedule(user_id=user.id,agent_id='printing',enabled=True,thread_id='main'));db.commit()
    with engine.begin() as connection:generalize_workspace(connection)
    with Session(engine) as db:
        assert not db.get(AgentSchedule,(user_id,'resources')).enabled
        job=AgentJob(user_id=user_id,agent_id='resources',prompt='Current request');db.add(job);db.flush();job_id=job.id;db.commit()
    with engine.begin() as connection:generalize_workspace(connection)
    with Session(engine) as db:assert db.get(AgentJob,job_id).status=='pending'
    engine.dispose()
