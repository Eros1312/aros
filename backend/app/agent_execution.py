"""Durable bounded preparation owned by Aros, not a second inference agent loop."""
import copy
from datetime import datetime,timedelta,timezone
from typing import Annotated,Literal
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,StrictBool,ConfigDict
from sqlalchemy import select,update
from .database import AgentJob,Item,User,SessionLocal,get_db,now
from .security import current_user,audit
from .services import item_dict
from .agent_capabilities import CAPABILITY,TOOLS,CapabilityBlocked,digest,read_resources,revalidate_sources,selection_content

router=APIRouter()
UserDep=Annotated[User,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]
class ExecutionIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    goal:str=Field(min_length=10,max_length=4000)
    title:str=Field(min_length=1,max_length=160)
    area:Literal['university','prisma','technology','printing','personal','server']
    query:str=Field(default='',max_length=160)
    item_ids:list[str]=Field(default_factory=list,max_length=20)
    project_id:str|None=None
    authorize_internal_write:StrictBool
    idempotency_key:UUID
    step_budget:int=Field(default=3,ge=1,le=3)

class UndoIn(BaseModel):
    version:int=Field(ge=1)

def describe(job,db,user):
    from .openclaw import job_dict
    return job_dict(job,db,user)

def scope_of(command):return {key:command[key] for key in ['area','query','item_ids','project_id']}

def validate_project(db,user,scope):
    if not scope.get('project_id'):return
    from .agent_capabilities import source_allowed
    row=db.scalar(select(Item).where(Item.id==scope['project_id'],Item.user_id==user.id).with_for_update().execution_options(populate_existing=True))
    if not row or row.kind not in ['project','event'] or not source_allowed(db,row,user.id,scope['area']):raise CapabilityBlocked('project_unavailable','El proyecto no está disponible en este ámbito.')

@router.post('/executions',status_code=202)
def enqueue_execution(payload:ExecutionIn,user:UserDep,db:DbDep):
    if not payload.authorize_internal_write:raise HTTPException(409,'Autoriza guardar únicamente esta selección interna; los originales no se modifican.')
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    if not db.scalar(select(User.ai_consent).where(User.id==user.id)):raise HTTPException(409,'El consentimiento para trabajo asistido está revocado.')
    command=payload.model_dump(mode='json',exclude={'idempotency_key'})
    command['goal']=command['goal'].strip();command['title']=command['title'].strip();command['item_ids']=list(dict.fromkeys(command['item_ids']))
    if not command['goal'] or not command['title']:raise HTTPException(422,'Indica objetivo y título.')
    if any(len(id)>100 for id in command['item_ids']) or (command['project_id'] and len(command['project_id'])>100):raise HTTPException(422,'Referencia inválida')
    hashed=digest(command);key=str(payload.idempotency_key)
    prior=db.scalar(select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.command_key==key))
    if prior:
        if (prior.execution or {}).get('authorization',{}).get('command_hash')!=hashed:raise HTTPException(409,'Esta clave ya autoriza otra petición. Usa una clave nueva para un alcance diferente.')
        return describe(prior,db,user)
    try:validate_project(db,user,scope_of(command))
    except CapabilityBlocked as exc:raise HTTPException(409,exc.message) from None
    count=len(list(db.scalars(select(AgentJob.id).where(AgentJob.user_id==user.id,AgentJob.is_review==False,AgentJob.status.in_(['pending','running'])))))
    if count>=3:raise HTTPException(409,'Hay tres trabajos interactivos en curso. Espera a que terminen.')
    instant=now();deadline=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    execution={'capability':CAPABILITY,'state':'queued','phase':'read','state_version':1,'attempt_id':str(uuid4()),'recovery_count':0,'plan_version':1,'command':command,'normalized_goal':command['goal'],'scope':scope_of(command),'authorization':{'capability':CAPABILITY,'user_id':user.id,'authorized_at':instant,'expires_at':deadline,'command_hash':hashed,'source':'explicit_user_command'},'acceptance_criteria':['Leer fuentes disponibles del propietario y ámbito elegido','Guardar una única nota interna basada en la lectura anterior','Verificar propietario, contenido, hash y versiones de fuentes'],'budget':{'step_limit':payload.step_budget,'steps_used':0,'model_call_limit':0,'model_calls':0,'tokens':None,'cost':None,'deadline_at':deadline,'status':'available'},'steps':[],'evidence':[],'artifact_id':None,'error_code':None,'recovery_hint':None}
    job=AgentJob(user_id=user.id,agent_id='aros',thread_id=str(uuid4()),model='local',prompt=command['goal'],command_key=key,execution=execution,events=[{'id':str(uuid4()),'type':'accepted','text':'Petición aceptada. Aún no se ha creado ni verificado el resultado.','at':instant}]);db.add(job);db.flush();audit(db,user,'agent.execution_authorized',job.id);db.commit();return describe(job,db,user)

def owned_job(db,user,id,lock=False):
    query=select(AgentJob).where(AgentJob.id==id,AgentJob.user_id==user.id)
    if lock:query=query.with_for_update()
    job=db.scalar(query.execution_options(populate_existing=True))
    if not job or not job.execution:raise HTTPException(404,'Ejecución no encontrada')
    return job

def artifact_for(db,user,job):
    execution=job.execution
    item=db.scalar(select(Item).where(Item.id==execution.get('artifact_id'),Item.user_id==user.id).execution_options(populate_existing=True)) if execution.get('artifact_id') else None
    if not item or item.user_id!=user.id or item.area!=execution['scope']['area'] or item.source!='agent-execution' or item.external_id!=job.id:raise HTTPException(404,'Resultado no encontrado')
    return item

@router.get('/executions/{job_id}/artifact')
def artifact(job_id:str,user:UserDep,db:DbDep):
    job=owned_job(db,user,job_id);item=artifact_for(db,user,job)
    if item.status=='archived':raise HTTPException(409,'La selección se ha deshecho o archivado.')
    try:revalidate_sources(db,user,job.execution['scope'],job.execution.get('selected_sources',[]),lock=False)
    except CapabilityBlocked as exc:raise HTTPException(409,exc.message) from None
    references=[e for e in job.execution.get('evidence',[]) if e.get('type')=='source']
    if item.version!=job.execution.get('artifact_version') or digest(item.description)!=job.execution.get('artifact_hash') or (item.data or {}).get('source_references')!=references or (item.data or {}).get('project_id')!=job.execution['scope'].get('project_id'):
        raise HTTPException(409,'La selección cambió desde su comprobación; revisa el recurso actual antes de usar este resultado.')
    return item_dict(item)

@router.post('/executions/{job_id}/undo')
def undo(job_id:str,payload:UndoIn,user:UserDep,db:DbDep):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    job=owned_job(db,user,job_id,True);item=artifact_for(db,user,job)
    if item.status=='archived' and (job.execution or {}).get('undone_at'):return {'id':item.id,'undone':True,'version':item.version}
    if item.version!=payload.version:raise HTTPException(409,'La selección cambió. Revisa su versión antes de deshacer.')
    expected=job.execution.get('artifact_hash')
    references=[e for e in job.execution.get('evidence',[]) if e.get('type')=='source']
    if not expected or digest(item.description)!=expected or (item.data or {}).get('source_references')!=references or (item.data or {}).get('project_id')!=job.execution['scope'].get('project_id'):raise HTTPException(409,'El contenido o sus referencias cambiaron; no se deshace sin revisar.')
    changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==payload.version).values(status='archived',version=Item.version+1,updated_at=now()))
    if changed.rowcount!=1:raise HTTPException(409,'La selección cambió')
    execution=copy.deepcopy(job.execution);execution['undone_at']=now();execution['state_version']+=1;job.execution=execution
    audit(db,user,'agent.execution_undone',job.id);db.commit();return {'id':item.id,'undone':True,'version':payload.version+1}

def persist(db,job,lease,execution,state,summary,status=None):
    execution['state']=state;execution['state_version']+=1;execution['last_progress_at']=now()
    terminal=state in ['completed','partially_completed','waiting_information','failed','cancelled']
    changed=db.execute(update(AgentJob).where(AgentJob.id==job.id,AgentJob.status=='running',AgentJob.lease_id==lease).values(execution=execution,status=status or ('completed' if state=='completed' else 'failed' if terminal else 'pending'),finished_at=now() if terminal else None,heartbeat_at=now(),reply=summary if terminal else '',error=summary if state in ['failed','waiting_information'] else '',events=[*(job.events or [])[-59:],{'id':str(uuid4()),'type':state,'text':summary,'at':now()}]))
    if changed.rowcount!=1:db.rollback();return False
    db.commit();return True

def process_execution_step(db,job,lease):
    # User and job locks serialize permission revocation/cancel with each effect.
    user=db.scalar(select(User).where(User.id==job.user_id).with_for_update().execution_options(populate_existing=True))
    job=db.scalar(select(AgentJob).where(AgentJob.id==job.id).with_for_update().execution_options(populate_existing=True))
    if not job or job.status!='running' or job.lease_id!=lease:return
    execution=copy.deepcopy(job.execution)
    try:
        auth=execution.get('authorization',{});command=execution.get('command',{});scope=execution.get('scope',{})
        if not user or not user.ai_consent:raise CapabilityBlocked('consent_revoked','El consentimiento está revocado; no se ejecutaron nuevos pasos.')
        if execution.get('capability')!=CAPABILITY or auth.get('capability')!=CAPABILITY or auth.get('user_id')!=user.id or digest(command)!=auth.get('command_hash') or scope!=scope_of(command) or not command.get('authorize_internal_write'):raise CapabilityBlocked('authorization_invalid','La autorización no coincide con esta operación; no se amplió el permiso.')
        if datetime.fromisoformat(auth['expires_at'])<=datetime.now(timezone.utc) or datetime.fromisoformat(execution['budget']['deadline_at'])<=datetime.now(timezone.utc):raise CapabilityBlocked('budget_exhausted','Se agotó el tiempo autorizado; se conserva el progreso.')
        validate_project(db,user,scope)
        budget=execution['budget']
        if budget['step_limit']!=command['step_budget'] or budget['model_call_limit']!=0 or budget['model_calls']!=0:raise CapabilityBlocked('authorization_invalid','El presupuesto no coincide con la autorización original.')
        if budget['steps_used']>=budget['step_limit']:raise CapabilityBlocked('budget_exhausted','Se agotó el presupuesto de pasos; se conserva el progreso sin nuevas llamadas.')
        phase=execution['phase'];artifacts=[]
        if phase=='read':
            sources=read_resources(db,user,scope);execution['selected_sources']=sources
            execution['evidence']=[{'type':'source','id':r['id'],'version':r['version'],'content_hash':r['content_hash']} for r in sources]
            summary=f'Consultados {len(sources)} recursos accesibles del ámbito elegido.';execution['phase']='write'
        elif phase=='write':
            sources=execution.get('selected_sources',[]);revalidate_sources(db,user,scope,sources)
            if not sources:raise CapabilityBlocked('no_results','No hay resultados de lectura; no se creó una nota vacía.')
            body=selection_content(command['goal'],sources);hash=digest(body);id=str(uuid5(NAMESPACE_URL,'aros-selection:'+job.id))
            item=db.get(Item,id)
            if item:
                if item.user_id!=user.id or item.area!=scope['area'] or item.external_id!=job.id or item.source!='agent-execution' or digest(item.description)!=hash:raise CapabilityBlocked('artifact_conflict','Existe un resultado diferente; no se sobrescribió.')
            else:
                item=Item(id=id,user_id=user.id,kind='note',area=scope['area'],source='agent-execution',external_id=job.id,title=command['title'],description=body,status='active',data={'execution_id':job.id,'capability':CAPABILITY,'project_id':scope.get('project_id'),'source_references':execution['evidence'],'content_hash':hash,'review_state':'draft','originals_unchanged':True});db.add(item);db.flush()
            execution['artifact_id']=item.id;execution['artifact_hash']=hash;execution['artifact_version']=item.version;execution['phase']='verify';artifacts=[item.id];summary='Selección interna guardada. Su verificación está pendiente.'
        elif phase=='verify':
            sources=execution.get('selected_sources',[]);revalidate_sources(db,user,scope,sources);item=artifact_for(db,user,job)
            if item.status!='active' or item.version!=execution.get('artifact_version') or digest(item.description)!=execution.get('artifact_hash') or item.data.get('source_references')!=execution['evidence'] or item.data.get('project_id')!=scope.get('project_id'):raise CapabilityBlocked('verification_failed','El resultado no coincide con el contenido y ámbito guardados.')
            execution['phase']='verified';execution['verified_at']=now();execution['evidence'].append({'type':'artifact','id':item.id,'version':item.version,'content_hash':execution['artifact_hash'],'checks':['owner','scope','content_hash','source_versions','project']});artifacts=[item.id];summary='Selección interna creada y verificada. Puedes abrirla; los originales permanecen intactos.'
        else:raise CapabilityBlocked('invalid_phase','Fase no reconocida. La operación se detuvo.')
        budget['steps_used']+=1
        execution['steps'].append({'id':str(uuid4()),'tool':TOOLS[('read','write','verify').index(phase)],'status':'success','summary':summary,'next_actions':[] if phase=='verify' else [execution['phase']],'artifacts':artifacts,'at':now()})
        if phase=='verify':budget['status']='satisfied'
        persist(db,job,lease,execution,'completed' if phase=='verify' else 'running',summary)
    except CapabilityBlocked as exc:
        execution['error_code']=exc.code;execution['recovery_hint']='Revisa el alcance, permisos o presupuesto y autoriza una petición nueva; los originales no cambian.'
        if exc.code=='budget_exhausted':execution['budget']['status']='exhausted'
        state='partially_completed' if execution.get('artifact_id') else 'waiting_information' if exc.code in ['no_results','source_unavailable','source_changed','project_unavailable'] else 'failed'
        persist(db,job,lease,execution,state,exc.message)

def recover_executions(db,stale):
    rows=list(db.scalars(select(AgentJob).where(AgentJob.status=='running',AgentJob.command_key.is_not(None),stale).with_for_update()))
    for job in rows:
        execution=copy.deepcopy(job.execution or {})
        if execution.get('capability')!=CAPABILITY:continue
        if execution.get('recovery_count',0)>=2:
            execution['error_code']='recovery_exhausted';execution['state']='partially_completed' if execution.get('artifact_id') else 'failed';execution['recovery_hint']='Revisa el resultado conservado y solicita una nueva preparación.';job.status='failed';job.error='Se agotaron los dos reinicios seguros.';job.finished_at=now()
        else:
            execution['recovery_count']=execution.get('recovery_count',0)+1;execution['attempt_id']=str(uuid4());execution['state']='queued';job.status='pending';job.lease_id=None;job.retry_after=None
        execution['state_version']+=1;job.execution=execution
    db.commit()


def fail_execution(db,job,lease):
    execution=copy.deepcopy(job.execution)
    execution['error_code']='execution_failed';execution['recovery_hint']='El paso no se completó; se conserva el resultado confirmado. Revisa antes de autorizar una petición nueva.'
    persist(db,job,lease,execution,'partially_completed' if execution.get('artifact_id') else 'failed','No se pudo verificar este paso. Los originales no se han modificado.')
