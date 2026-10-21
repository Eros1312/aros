"""Durable liveness, capacity isolation and bounded safe recovery."""
import time
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app import openclaw as bridge
from app import agent_runtime as runtime
from app.agent_schedules import enqueue_due_reviews
from app.database import User,AgentJob,AgentSchedule,SessionLocal,WorkerHeartbeat,Automation,Run
from app.config import settings
from app.worker import tick
from test_api import client,auth

@pytest.fixture
def enabled(auth,monkeypatch):
    monkeypatch.setattr(settings,'openclaw_url','http://gateway:18789');monkeypatch.setattr(settings,'openclaw_token','offline')
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    return auth

def owner(db):return db.scalar(select(User).where(User.email=='aros@local'))
def scheduled(db,user):
    thread=str(uuid4());db.add(AgentSchedule(user_id=user.id,agent_id='personal',enabled=True,interval_minutes=60,next_run='2020-01-01T00:00:00+00:00',thread_id=thread));return thread

def test_runtime_authenticated_owner_and_real_liveness(enabled):
    with SessionLocal() as db:
        u=owner(db);other=User(email='private-runtime@local',password_hash='unused');db.add(other);db.flush()
        db.add(AgentJob(user_id=other.id,agent_id='personal',prompt='Private prompt',status='failed',error='Private error'))
        db.add(WorkerHeartbeat(name='agent-interactive',instance_id='test',heartbeat_at=datetime.now(timezone.utc).isoformat(),last_cycle_at=datetime.now(timezone.utc).isoformat(),last_error=''))
        db.add(WorkerHeartbeat(name='agent-review',instance_id='test',heartbeat_at='2020-01-01T00:00:00+00:00',last_cycle_at='2020-01-01T00:00:00+00:00',last_error=''));db.commit()
    response=enabled.get('/api/v1/agents/runtime');assert response.status_code==200
    body=response.json();assert [w['state'] for w in body['workers']]==['online','stale','unknown']
    assert 'Private' not in response.text and body['queue']=={'interactive':0,'review':0,'running':0}
    enabled.post('/api/v1/auth/logout');assert enabled.get('/api/v1/agents/runtime').status_code==401

def test_reviews_do_not_starve_valid_owner(enabled):
    with SessionLocal() as db:
        for i in range(12):
            u=User(email=f'no-consent-{i}@local',password_hash='unused',ai_consent=False);db.add(u);db.flush();scheduled(db,u)
        u=owner(db);scheduled(db,u);db.commit()
    assert enqueue_due_reviews()==1
    assert enabled.get('/api/v1/agents/runtime').json()['queue']['review']==1

def test_review_capacity_and_interactive_priority(enabled,monkeypatch):
    with SessionLocal() as db:
        u=owner(db);thread=scheduled(db,u)
        review=AgentJob(user_id=u.id,agent_id='personal',thread_id=thread,is_review=True,prompt='Review',created_at='2020-01-01T00:00:00+00:00');db.add(review);db.commit()
    for agent in ['aros','technology','server']:assert enabled.post('/api/v1/agents/runs',json={'agent_id':agent,'text':'Interactive'}).status_code==202
    assert enabled.post('/api/v1/agents/runs',json={'agent_id':'resources','text':'Fourth'}).status_code==409
    called=[]
    def complete(user,prompt,records,agent_id,**kwargs):called.append(prompt);return 'Done',[]
    monkeypatch.setattr(bridge,'completion',complete);bridge.process_jobs(limit=1)
    assert called==['Interactive']
    bridge.process_jobs(review_only=True,limit=1);assert called[-1]=='Review'

def test_stale_job_not_replayed_and_fresh_lease_survives(enabled,monkeypatch):
    old=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat();current=datetime.now(timezone.utc).isoformat()
    with SessionLocal() as db:
        u=owner(db);stale=AgentJob(user_id=u.id,agent_id='aros',prompt='Interrupted',status='running',started_at=old,heartbeat_at=old,lease_id='old')
        fresh=AgentJob(user_id=u.id,agent_id='aros',prompt='Alive',status='running',started_at=current,heartbeat_at=current,lease_id='new');db.add_all([stale,fresh]);db.commit();ids=(stale.id,fresh.id)
    monkeypatch.setattr(bridge,'completion',lambda *a,**k:pytest.fail('Cannot replay running inference'))
    bridge.process_jobs()
    with SessionLocal() as db:assert db.get(AgentJob,ids[0]).status=='failed' and db.get(AgentJob,ids[1]).status=='running'

def test_review_429_one_delayed_retry_and_interactive_no_retry(enabled,monkeypatch):
    with SessionLocal() as db:
        u=owner(db);thread=scheduled(db,u);review=AgentJob(user_id=u.id,agent_id='personal',thread_id=thread,is_review=True,prompt='Review');db.add(review);db.commit();id=review.id
    calls=[]
    def limited(*args,**kwargs):calls.append(1);raise HTTPException(429,'Provider limit')
    monkeypatch.setattr(bridge,'completion',limited);bridge.process_jobs(review_only=True)
    with SessionLocal() as db:
        j=db.get(AgentJob,id);assert j.status=='pending' and j.attempt_count==1 and j.retry_after>datetime.now(timezone.utc).isoformat()
    bridge.process_jobs(review_only=True);assert len(calls)==1
    with SessionLocal() as db:j=db.get(AgentJob,id);j.retry_after='2020-01-01T00:00:00+00:00';db.commit()
    bridge.process_jobs(review_only=True)
    with SessionLocal() as db:assert db.get(AgentJob,id).status=='failed' and db.get(AgentJob,id).attempt_count==2
    j=enabled.post('/api/v1/agents/runs',json={'text':'Interactive'}).json();bridge.process_jobs(review_only=False)
    assert enabled.get('/api/v1/agents/runs/'+j['id']).json()['status']=='failed'

def test_consent_revoked_during_completion_cannot_save(enabled,monkeypatch):
    j=enabled.post('/api/v1/agents/runs',json={'text':'Hello'}).json()
    def complete(*args,**kwargs):
        with SessionLocal() as db:u=owner(db);u.ai_consent=False;db.commit()
        return 'Should not complete',[]
    monkeypatch.setattr(bridge,'completion',complete);bridge.process_jobs()
    result=enabled.get('/api/v1/agents/runs/'+j['id']).json();assert result['status']=='failed' and result['reply']==''

def test_paused_schedule_not_executed_and_manual_run_allowed(enabled):
    with SessionLocal() as db:
        u=owner(db);a=Automation(user_id=u.id,title='Paused',kind='deadline_review',enabled=False,next_run='2020-01-01T00:00:00+00:00');db.add(a);db.flush()
        scheduled_run=Run(user_id=u.id,automation_id=a.id);manual=Run(user_id=u.id,automation_id=a.id,origin='manual');db.add_all([scheduled_run,manual]);db.commit();ids=(scheduled_run.id,manual.id)
    tick()
    with SessionLocal() as db:assert db.get(Run,ids[0]).status=='cancelled' and db.get(Run,ids[1]).status=='completed'

def test_heartbeat_durable_while_inference_blocked(enabled,monkeypatch):
    monkeypatch.setattr(runtime,'HEARTBEAT_SECONDS',0.02)
    with SessionLocal() as db:
        u=owner(db);j=AgentJob(user_id=u.id,agent_id='aros',prompt='Long',status='running',started_at=datetime.now(timezone.utc).isoformat(),heartbeat_at='2020-01-01T00:00:00+00:00',lease_id='test');db.add(j);db.commit();id=j.id
    with runtime.job_pulse(id,'test'):
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            with SessionLocal() as db:
                if runtime.fresh(db.get(AgentJob,id).heartbeat_at):break
            time.sleep(0.02)
        else:pytest.fail('Heartbeat did not persist during blocked inference')
    with SessionLocal() as db:assert db.get(AgentJob,id).status=='running'

def test_old_lease_cannot_overwrite_new_claim(enabled,monkeypatch):
    j=enabled.post('/api/v1/agents/runs',json={'text':'Lease'}).json()
    def delayed(*args,**kwargs):
        with SessionLocal() as db:
            row=db.get(AgentJob,j['id']);row.lease_id='new-claim';row.reply='New owner partial';db.commit()
        return 'Old worker result',[]
    monkeypatch.setattr(bridge,'completion',delayed);bridge.process_jobs(limit=1)
    result=enabled.get('/api/v1/agents/runs/'+j['id']).json();assert result['status']=='running' and result['reply']=='New owner partial'

def test_non_rate_limit_errors_not_retried(enabled,monkeypatch):
    with SessionLocal() as db:
        u=owner(db);thread=scheduled(db,u);j=AgentJob(user_id=u.id,agent_id='personal',thread_id=thread,is_review=True,prompt='Review');db.add(j);db.commit();id=j.id
    def unavailable(*args,**kwargs):raise HTTPException(502,'Connection failed')
    monkeypatch.setattr(bridge,'completion',unavailable);bridge.process_jobs(review_only=True)
    with SessionLocal() as db:assert db.get(AgentJob,id).status=='failed' and db.get(AgentJob,id).retry_after is None

def test_worker_heartbeat_during_long_cycle_and_stalled_state(enabled,monkeypatch):
    monkeypatch.setattr(runtime,'HEARTBEAT_SECONDS',0.02)
    with runtime.WorkerPulse('agent-interactive'):
        time.sleep(0.06)
        with SessionLocal() as db:
            row=db.get(WorkerHeartbeat,'agent-interactive');assert runtime.worker_state(row)=='online'
            row.last_cycle_at='2020-01-01T00:00:00+00:00';assert runtime.worker_state(row)=='stalled'

def test_busy_reviews_defer_without_monopolizing_due_window(enabled):
    with SessionLocal() as db:
        for i in range(10):
            u=User(email=f'busy-review-{i}@local',password_hash='unused',ai_consent=True);db.add(u);db.flush();thread=scheduled(db,u)
            db.add(AgentJob(user_id=u.id,agent_id='personal',thread_id=thread,is_review=True,prompt='Busy'))
        scheduled(db,owner(db));db.commit()
    assert enqueue_due_reviews()==0
    assert enqueue_due_reviews()==1
