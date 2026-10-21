"""Durable liveness separate from gateway reachability and inference success."""
import logging,threading,time,os
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy import select,update
from .database import SessionLocal,WorkerHeartbeat,AgentJob,User,AgentSchedule,now
logger=logging.getLogger('aros.runtime')
HEARTBEAT_SECONDS=10
STALE_SECONDS=60
JOB_TIMEOUT_SECONDS=300

def fresh(value,seconds=STALE_SECONDS):
    try:return bool(value) and datetime.fromisoformat(value)>=datetime.now(timezone.utc)-timedelta(seconds=seconds)
    except (ValueError,TypeError):return False

def worker_state(row):
    if not row:return 'unknown'
    if not fresh(row.heartbeat_at):return 'stale'
    if not fresh(row.last_cycle_at,600 if row.name.startswith('agent-') else 1200):return 'stalled'
    return 'online'

class WorkerPulse:
    def __init__(self,name):self.name=name;self.instance=str(uuid4());self.stop=threading.Event();self.last_cycle=time.monotonic()
    def pulse(self,cycle=False,error=None):
        with SessionLocal() as db:
            row=db.get(WorkerHeartbeat,self.name)
            if not row:
                row=WorkerHeartbeat(name=self.name,instance_id=self.instance,heartbeat_at=now(),last_cycle_at=now());db.add(row)
            else:
                row.instance_id=self.instance;row.heartbeat_at=now()
            if cycle:row.last_cycle_at=now();self.last_cycle=time.monotonic()
            if error is not None:row.last_error=error
            db.commit()
    def loop(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:self.pulse()
            except Exception:logger.warning('Cannot persist worker heartbeat')
    def watchdog(self):
        # Separate from DB I/O so a stalled connection cannot stall recovery.
        budget=600 if self.name.startswith('agent-') else 1200
        while not self.stop.wait(HEARTBEAT_SECONDS):
            if time.monotonic()-self.last_cycle>budget:os._exit(1)
    def __enter__(self):
        self.pulse(cycle=True,error='');self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start();self.guard=threading.Thread(target=self.watchdog,daemon=True);self.guard.start();return self
    def __exit__(self,*args):self.stop.set();self.thread.join(timeout=2);self.guard.join(timeout=2)

@contextmanager
def job_pulse(job_id,lease):
    stop=threading.Event()
    def loop():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                with SessionLocal() as db:
                    job=db.scalar(select(AgentJob).where(AgentJob.id==job_id,AgentJob.status=='running',AgentJob.lease_id==lease))
                    if not job:return
                    consent=db.scalar(select(User.ai_consent).where(User.id==job.user_id))
                    plan=db.get(AgentSchedule,(job.user_id,job.agent_id)) if job.is_review else None
                    if not consent or (job.is_review and (not plan or not plan.enabled)):
                        db.execute(update(AgentJob).where(AgentJob.id==job_id,AgentJob.status=='running',AgentJob.lease_id==lease).values(status='cancelled',finished_at=now(),error='La consulta se canceló al revocar consentimiento o pausar la revisión.'))
                        db.commit();return
                    if not fresh(job.started_at,JOB_TIMEOUT_SECONDS):
                        db.execute(update(AgentJob).where(AgentJob.id==job_id,AgentJob.status=='running',AgentJob.lease_id==lease).values(status='failed',finished_at=now(),error='La consulta superó cinco minutos. No se reenvió automáticamente.'))
                        db.commit();return
                    db.execute(update(AgentJob).where(AgentJob.id==job_id,AgentJob.status=='running',AgentJob.lease_id==lease).values(heartbeat_at=now()));db.commit()
            except Exception:logger.warning('Cannot persist inference heartbeat')
    thread=threading.Thread(target=loop,daemon=True);thread.start()
    try:yield
    finally:stop.set();thread.join(timeout=2)

def healthcheck(name):
    with SessionLocal() as db:
        if worker_state(db.get(WorkerHeartbeat,name))!='online':raise SystemExit(1)
