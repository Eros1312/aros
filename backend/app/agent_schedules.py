"""Opt-in area reviews. One bounded model turn; proposals always require review."""
from datetime import datetime,timedelta,timezone
from uuid import uuid5,NAMESPACE_URL
from typing import Annotated
import hashlib,json
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,update
from .database import AgentSchedule,AgentJob,User,Item,Connector,SessionLocal,get_db,now
from .security import current_user,audit
from .config import settings

PROMPTS={
 'aros':'Revisa los recursos actuales de mis áreas. Resume cambios relevantes y prioridades; prepara propuestas locales solo cuando estén justificadas. Si no hay nada relevante, responde brevemente.',
 'learning':'Revisa mis lecturas, notas, objetivos y tareas de aprendizaje. Resume dudas y próximos pasos basados en los recursos actuales.',
 'events':'Revisa proyectos y tareas Eventos actuales. Resume bloqueos y próximos pasos concretos; prepara propuestas locales para mi revisión.',
 'technology':'Revisa mis notas y proyectos de tecnología. Destaca novedades disponibles en los recursos actuales y próximos pasos concretos.',
 'resources':'Revisa documentos, notas, enlaces y proyectos guardados. Identifica información pendiente de organizar y prepara propuestas locales para mi revisión.',
 'server':'Revisa recursos y tareas de servidor. Señala incidencias documentadas y prioridades; no ejecutes comandos ni reinicies servicios.',
 'personal':'Revisa agenda, recordatorios y pendientes personales. Resume fechas próximas y prioridades; no envíes mensajes a terceros.'}
router=APIRouter()
class ScheduleIn(BaseModel):
    enabled:bool
    interval_minutes:int=Field(default=720,ge=60,le=10080)

def describe(row):return {**{k:getattr(row,k) for k in ['agent_id','enabled','interval_minutes','next_run','thread_id']},'inference_policy':'changes_or_daily','policy_description':'Se comprueban cambios en cada revisión. La IA interviene si cambia el contenido, se acerca un plazo o empieza un nuevo día.'}

def review_fingerprint(db,user,agent_id,at=None):
    """Local change detection; no prompt, credentials or document bytes are stored."""
    from .source_access import visible_items_clause
    at=at or datetime.now(timezone.utc)
    query=select(Item.id,Item.version,Item.title,Item.description,Item.status,Item.priority,Item.due_at,Item.data).where(
        Item.user_id==user.id,Item.source!='agent-review',Item.status.notin_(['archived','cancelled','canceled','withdrawn']),visible_items_clause(db,user))
    if agent_id!='aros':query=query.where(Item.area==agent_id)
    digest=hashlib.sha256()
    digest.update(json.dumps({'day':at.astimezone(ZoneInfo('Europe/Madrid')).date().isoformat(),'model':user.ai_model},sort_keys=True).encode())
    for row in db.execute(query.order_by(Item.id)).yield_per(100):
        ident,version,title,description,status,priority,due,data=row
        # Crossing a deadline must wake a review even when its resource did not change.
        bucket=None
        if due and status not in ['done','delivered']:
            try:
                deadline=datetime.fromisoformat(due.replace('Z','+00:00'))
                if deadline.tzinfo is None:deadline=deadline.replace(tzinfo=ZoneInfo('Europe/Madrid'))
                seconds=(deadline-at).total_seconds()
                bucket='overdue' if seconds<=0 else 'hour' if seconds<=3600 else 'today' if seconds<=86400 else 'soon' if seconds<=172800 else 'later'
            except (ValueError,TypeError):bucket='invalid'
        digest.update(json.dumps([ident,version,title,description,status,priority,due,data,bucket],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
    # Availability changes can matter even when an area has no saved documents.
    relevant={'learning':{'drive','calendar'},'events':{'canva'},'resources':{'drive','notion'},'technology':{'rss','github'},'personal':{'gmail','calendar','drive','weather'},'server':{'server','github','vercel'}}
    for ident,status in db.execute(select(Connector.id,Connector.status).where(Connector.user_id==user.id).order_by(Connector.id)):
        if agent_id=='aros' or ident in relevant.get(agent_id,set()):digest.update(json.dumps([ident,status]).encode())
    return digest.hexdigest()

@router.get('/schedules')
def schedules(user:Annotated[User,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    return [describe(row) for row in db.scalars(select(AgentSchedule).where(AgentSchedule.user_id==user.id))]

@router.patch('/schedules/{agent_id}')
def configure(agent_id:str,payload:ScheduleIn,user:Annotated[User,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    if agent_id not in PROMPTS:raise HTTPException(404,'Agente desconocido')
    if payload.enabled and not user.ai_consent:raise HTTPException(409,'Activa IA externa antes de programar revisiones')
    if payload.enabled and not(settings.openclaw_url and settings.openclaw_token):raise HTTPException(409,'Configura OpenClaw antes de programar revisiones')
    row=db.get(AgentSchedule,(user.id,agent_id))
    if not row:
        row=AgentSchedule(user_id=user.id,agent_id=agent_id,thread_id=str(uuid5(NAMESPACE_URL,f'aros-review:{user.id}:{agent_id}')));db.add(row)
    row.enabled=payload.enabled;row.interval_minutes=payload.interval_minutes;row.next_run=now()
    if not payload.enabled:
        queued=list(db.scalars(select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.agent_id==agent_id,AgentJob.thread_id==row.thread_id,AgentJob.status.in_(['pending','running']))))
        for job in queued:
            db.execute(update(AgentJob).where(AgentJob.id==job.id,AgentJob.status.in_(['pending','running'])).values(status='cancelled',finished_at=now(),events=[*(job.events or [])[-59:],{'type':'cancelled','text':'Revisión cancelada al pausar la programación','at':now()}]))
    audit(db,user,'agent.schedule_enabled' if payload.enabled else 'agent.schedule_paused',agent_id);db.commit()
    return describe(row)

def enqueue_due_reviews():
    if not(settings.openclaw_url and settings.openclaw_token):return 0
    count=0
    with SessionLocal() as db:
        due=list(db.scalars(select(AgentSchedule).join(User,User.id==AgentSchedule.user_id).where(AgentSchedule.enabled==True,User.ai_consent==True,AgentSchedule.next_run<=now()).order_by(AgentSchedule.next_run).limit(10)))
        for row in due:
            user=db.get(User,row.user_id)
            if not user or not user.ai_consent:continue
            # Share the same per-user capacity bound as interactive conversations.
            db.scalar(select(User).where(User.id==user.id).with_for_update())
            busy=list(db.scalars(select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.status.in_(['pending','running']))))
            if sum(not job.is_review for job in busy)>=3 or any(job.is_review or job.agent_id==row.agent_id for job in busy):
                db.execute(update(AgentSchedule).where(AgentSchedule.user_id==row.user_id,AgentSchedule.agent_id==row.agent_id,AgentSchedule.next_run==row.next_run,AgentSchedule.enabled==True).values(next_run=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()))
                continue
            previous=row.next_run
            claimed=db.execute(update(AgentSchedule).where(AgentSchedule.user_id==user.id,AgentSchedule.agent_id==row.agent_id,AgentSchedule.enabled==True,AgentSchedule.next_run==previous).values(next_run=(datetime.now(timezone.utc)+timedelta(minutes=row.interval_minutes)).isoformat()))
            if claimed.rowcount!=1:continue
            fingerprint=review_fingerprint(db,user,row.agent_id)
            last=db.scalar(select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.agent_id==row.agent_id,AgentJob.thread_id==row.thread_id,AgentJob.is_review==True,AgentJob.status=='completed').order_by(AgentJob.created_at.desc(),AgentJob.id.desc()).limit(1))
            previous_fingerprint=next((event.get('fingerprint') for event in reversed(last.events or []) if event.get('type')=='review_input'),None) if last else None
            if previous_fingerprint==fingerprint:
                audit(db,user,'agent.review_unchanged',row.agent_id)
                continue
            db.add(AgentJob(is_review=True,user_id=user.id,agent_id=row.agent_id,thread_id=row.thread_id,model=user.ai_model,prompt=PROMPTS[row.agent_id],events=[{'type':'scheduled','text':'Revisión periódica activada por ti','at':now()},{'type':'review_input','text':'Cambios comprobados antes de consultar la IA','fingerprint':fingerprint,'at':now()}]))
            audit(db,user,'agent.schedule_queued',row.agent_id);count+=1
        db.commit()
    return count
