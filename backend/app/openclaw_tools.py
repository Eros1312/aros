"""Private single-owner WhatsApp bridge. Never accepts a user ID from a model."""
import secrets,re
from typing import Annotated
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from .database import User,AgentJob,get_db,now
from .config import settings
from .security import audit
from .agent_actions import collect_actions
from .ai import context_items
router=APIRouter(prefix='/api/v1/internal/openclaw',tags=['Private OpenClaw tools'])
DbDep=Annotated[object,Depends(get_db)]
def owner(db,authorization,sender):
    if not settings.openclaw_token or not secrets.compare_digest(authorization or '', 'Bearer '+settings.openclaw_token): raise HTTPException(401,'Acceso privado')
    user=db.scalar(select(User).where(User.email==settings.admin_email))
    if not user or not user.ai_consent: raise HTTPException(403,'IA externa no autorizada')
    from .channels import CONTROL
    from .database import Connector
    from .connectors import decode_tokens
    row=db.get(Connector,(CONTROL,user.id))
    if not row or row.sync_details.get('desired')!='enabled' or row.sync_details.get('pending') or not row.encrypted_tokens:
        raise HTTPException(403,'WhatsApp no autorizado')
    try:phone=decode_tokens(row.encrypted_tokens)['phone']
    except Exception:raise HTTPException(403,'WhatsApp no autorizado') from None
    if not isinstance(sender,str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}',sender) or not isinstance(phone,str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}',phone) or not secrets.compare_digest(sender,phone):raise HTTPException(403,'Remitente de WhatsApp no autorizado')
    return user
class QueryIn(BaseModel):
    query:str=Field(min_length=1,max_length=4000)
@router.post('/resources')
def resources(payload:QueryIn,db:DbDep,authorization:str|None=Header(default=None),x_aros_whatsapp_sender:str|None=Header(default=None)):
    user=owner(db,authorization,x_aros_whatsapp_sender)
    return {'resources':[{'id':i.id,'title':i.title,'area':i.area,'kind':i.kind,'description':i.description[:5000],'status':i.status,'due_at':i.due_at,'version':i.version} for i in context_items(db,user,payload.query)]}
class ActionIn(BaseModel):
    type:str=Field(max_length=30)
    payload:dict
@router.post('/prepare')
def prepare(payload:ActionIn,db:DbDep,authorization:str|None=Header(default=None),x_aros_whatsapp_sender:str|None=Header(default=None)):
    user=owner(db,authorization,x_aros_whatsapp_sender)
    actions=collect_actions([payload.model_dump()],'aros')
    if not actions: raise HTTPException(422,'Acción inválida')
    job=AgentJob(user_id=user.id,agent_id='aros',thread_id='main',model=user.ai_model,prompt='Acción preparada desde WhatsApp',status='completed',reply='Revisa la propuesta de WhatsApp y pulsa Aplicar para ejecutarla.',actions=actions,finished_at=now())
    db.add(job);db.flush();audit(db,user,'channel.action_prepared',job.id);db.commit()
    return {'status':'awaiting_review','run_id':job.id,'instructions':'Abre Agentes → Aros → Conversación principal y revisa la acción. Todavía no se ha aplicado.'}
