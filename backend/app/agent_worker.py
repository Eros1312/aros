"""Separate bounded interactive and review lanes; durable heartbeat during inference."""
import logging,time,threading,os
from .config import settings
from .openclaw import process_jobs
from .agent_schedules import enqueue_due_reviews
from .agent_runtime import WorkerPulse
logger=logging.getLogger('aros.agent_worker')

def lane(review):
    with WorkerPulse('agent-review' if review else 'agent-interactive') as pulse:
        while True:
            try:
                if review:enqueue_due_reviews()
                process_jobs(review_only=review,limit=1)
                pulse.pulse(cycle=True,error='')
            except Exception:
                logger.exception('Agent lane cycle failed')
                pulse.pulse(cycle=True,error='El ciclo falló. Se conserva la cola; revisa el servicio.')
            time.sleep(3)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO);settings.validate_production()
    thread=threading.Thread(target=lane,args=(True,),daemon=True);thread.start()
    interactive=threading.Thread(target=lane,args=(False,),daemon=True);interactive.start()
    while thread.is_alive() and interactive.is_alive():time.sleep(3)
    raise SystemExit(1)
