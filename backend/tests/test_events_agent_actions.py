"""Explicit conversational Canva routing and authorization race regressions."""
from urllib.parse import parse_qs,urlsplit
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,update
from sqlalchemy.orm import sessionmaker
from test_events import db,users,configured,connected
from app import events,openclaw as bridge
from app.agent_actions import collect_actions,apply_action,design_requested
from app.database import Item,AgentJob,Connector,User
from app.security import expiry

BRIEF='Crea un cartel Eventos usando el logo original aprobado'

def proposal(asset_id=None):return {'type':'events.design','payload':{'brief':BRIEF,'asset_id':asset_id}}

def test_collector_admits_only_aros_events_and_bounded_payloads():
    for agent in ['aros','events']:assert collect_actions([proposal()],agent)==[proposal()]
    for agent in ['learning','personal','server','resources','technology']:assert collect_actions([proposal()],agent)==[]
    for payload in [{'brief':'short'},{'brief':' '*20},{'brief':'x'*4001},{'brief':BRIEF,'model':'test/design-model'},{'brief':BRIEF,'user_id':'other'},{'brief':BRIEF,'asset_id':'x'*37}]:
        assert collect_actions([{'type':'events.design','payload':payload}],'aros')==[]
    assert collect_actions([proposal()],'events',allow_design=False)==[]
    assert design_requested('Necesito un cartel para Canva')
    assert design_requested('Crea pegatinas Eventos con el logo aprobado')
    assert not design_requested('Quiero que revises mis diseños de Canva')
    assert not design_requested('No crees un diseño Canva')

def test_apply_queue_and_receipt_are_atomic_and_model_preference_is_unchanged(db,users):
    owner,_=users;connected(db,owner);model=owner.ai_model
    item=apply_action(db,owner,proposal(),'events')
    assert item.status=='pending' and item.source=='canva-request' and item.data['model']==events.ADVANCED_MODEL
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Item).where(Item.source=='canva-request'))==0
    job=AgentJob(user_id=owner.id,agent_id='events',thread_id='main',model=model,prompt=BRIEF,status='completed',actions=[proposal()]);db.add(job);db.commit()
    first=bridge.execute_action(job.id,0,owner,db)
    again=bridge.execute_action(job.id,0,owner,db)
    assert first['id']==again['id'] and again['already_applied'] is True
    assert db.scalar(select(func.count()).select_from(Item).where(Item.source=='canva-request',Item.user_id==owner.id))==1
    assert db.get(User,owner.id).ai_model==model

def test_apply_enforces_owner_asset_area_consent_and_canva_setup(db,users):
    owner,other=users
    with pytest.raises(HTTPException) as failure:apply_action(db,owner,proposal(),'events')
    assert failure.value.status_code==409
    connected(db,owner)
    foreign=Item(user_id=other.id,kind='document',area='events',title='Foreign asset');db.add(foreign);db.commit()
    with pytest.raises(HTTPException) as failure:apply_action(db,owner,proposal(foreign.id),'aros')
    assert failure.value.status_code==404
    with pytest.raises(HTTPException) as failure:apply_action(db,owner,proposal(),'learning')
    assert failure.value.status_code==403
    owner.ai_consent=False;db.commit()
    with pytest.raises(HTTPException) as failure:apply_action(db,owner,proposal(),'events')
    assert failure.value.status_code==409

def test_disconnected_canva_proposal_explains_setup_and_periodic_reviews_cannot_route_design(db,users,monkeypatch):
    owner,_=users
    monkeypatch.setattr(bridge,'SessionLocal',sessionmaker(bind=db.bind,expire_on_commit=False))
    calls=[]
    def completion(user,prompt,records,agent_id,history=None,model=None,action_collector=None):
        calls.append(model);action_collector['actions']=[proposal()];return 'Revisa la propuesta y autoriza Canva en Eventos',[]
    monkeypatch.setattr(bridge,'completion',completion)
    manual=AgentJob(user_id=owner.id,agent_id='events',model=owner.ai_model,prompt=BRIEF)
    periodic=AgentJob(user_id=owner.id,agent_id='events',model=owner.ai_model,thread_id='reviews',prompt=BRIEF,events=[{'type':'scheduled','text':'Revisión','at':'2026-01-01'}])
    db.add_all([manual,periodic]);db.commit();bridge.process_jobs();db.expire_all()
    assert db.get(AgentJob,manual.id).actions[0]['setup_required'] is True
    assert 'Autoriza Canva' in db.get(AgentJob,manual.id).actions[0]['detail']
    assert db.get(AgentJob,periodic.id).actions==[]
    assert calls==[owner.ai_model,owner.ai_model] and owner.ai_model!=events.ADVANCED_MODEL
    assert db.scalar(select(func.count()).select_from(Item).where(Item.source=='canva-request'))==0

@pytest.mark.parametrize('change',['disconnect','application'])
def test_oauth_revision_change_during_token_exchange_is_not_resurrected(db,users,monkeypatch,change):
    owner,_=users;configured(db,owner)
    state=parse_qs(urlsplit(events.authorize(owner,db)['url']).query)['state'][0]
    def tokens(application,data):
        if change=='disconnect':db.execute(update(Connector).where(Connector.id=='canva',Connector.user_id==owner.id).values(status='disconnected',encrypted_tokens=None,revision=Connector.revision+1));db.commit()
        else:events.configure(events.ApplicationIn(client_id='changed-client',client_secret='changed-app-secret'),owner,db)
        return {'access_token':'old-app-token','refresh_token':'old-app-refresh','scope':' '.join(events.SCOPES),'expires_at':expiry(60)}
    monkeypatch.setattr(events,'token_request',tokens)
    with pytest.raises(HTTPException) as failure:events.callback(state,owner,db,code='code')
    assert failure.value.status_code==409
    db.expire_all();assert not db.get(Connector,('canva',owner.id)).encrypted_tokens

def test_consent_revoked_during_refresh_never_calls_advanced_generation(db,users,monkeypatch):
    owner,_=users;connected(db,owner,expired=True)
    created=events.design(events.BriefIn(brief=BRIEF),owner,db)
    def rotate(application,data):
        assert data['grant_type']=='refresh_token'
        owner.ai_consent=False;db.commit()
        return {'access_token':'rotated-token','refresh_token':'rotated-refresh','expires_at':expiry(60)}
    monkeypatch.setattr(events,'token_request',rotate)
    monkeypatch.setattr(events,'generate_plan',lambda *args:pytest.fail('Consent revoked before generation'))
    events.process_canva_jobs();db.expire_all()
    item=db.get(Item,created['id']);assert item.status=='cancelled'
