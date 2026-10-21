"""Durable CPU organizer generation, separate from unavailable image inference."""
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from .agent_runtime import worker_state
from .database import Audit, Item, SessionLocal, User, WorkerHeartbeat, get_db, now
from .printing_mesh import GENERATOR_VERSION, MAX_STL_BYTES, OrganizerParameters, generate_organizer, verify_stl
from .security import audit, current_user

router = APIRouter(prefix='/api/v1/printing/generation', tags=['Impresión 3D'])
UserDep = Annotated[User, Depends(current_user)]
DbDep = Annotated[object, Depends(get_db)]
SOURCE = 'printing-generation'
MAX_PENDING = 3
MAX_DAILY = 20
MAX_SAVED = 200
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


class GenerationIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parameters: OrganizerParameters
    idempotency_key: UUID
    project_id: UUID | None = None


def _references_current(db, user, refs):
    from .services import owned_item
    from .source_access import INACTIVE_STATES, resource_fingerprint
    if not isinstance(refs, list) or len(refs) > 20:
        raise HTTPException(409, 'Las fuentes del encargo no son válidas.')
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('id'), str):
            raise HTTPException(409, 'Las fuentes del encargo no son válidas.')
        item = owned_item(db, user, ref['id'])
        if item.status in INACTIVE_STATES or item.version != ref.get('version') or resource_fingerprint(item) != ref.get('content_hash'):
            raise HTTPException(409, 'Una fuente del encargo cambió o dejó de estar disponible.')


def _public(item):
    data = item.data or {}
    state = data.get('state', 'failed')
    verified = state == 'verified' and item.status == 'done'
    return {'id':item.id, 'title':item.title, 'state':state, 'parameters':data.get('parameters'),'project_id':data.get('project_id'),
            'created_at':item.created_at, 'finished_at':data.get('finished_at'),
            'attempt_count':data.get('attempt_count',0), 'error':data.get('error'),
            'evidence':data.get('evidence') if verified else None,
            'download_url':f'/api/v1/printing/generation/jobs/{item.id}/file' if verified else None,
            'manifest_url':f'/api/v1/printing/generation/jobs/{item.id}/manifest' if verified else None}


def queue_generation(db, user, parameters, idempotency_key, *, commit=True, source_references=None, project_id=None):
    """Shared acceptance path. commit=False keeps an agent receipt atomic with it."""
    try:
        params = OrganizerParameters.model_validate(parameters).model_dump()
        key = str(UUID(str(idempotency_key)))
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(422, 'Revisa medidas, divisiones e identificador del encargo.') from None
    refs = [] if source_references is None else source_references
    _references_current(db, user, refs)
    if project_id:
        from .services import owned_item
        from .source_access import INACTIVE_STATES
        project_id=str(project_id)
        project=owned_item(db,user,project_id)
        if project.kind!='project' or project.source not in ('manual','agent-conversation') or project.area!='printing' or project.status in INACTIVE_STATES:
            raise HTTPException(409,'El destino debe ser un proyecto interno disponible de Impresión 3D.')
    # PostgreSQL serializes each owner's capacity check and acceptance. The
    # unique external identity also prevents duplicate retries on every dialect.
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    prior = db.scalar(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source==SOURCE,Item.external_id==key))
    if prior:
        if prior.data.get('parameters') != params or prior.data.get('source_references',[]) != refs or prior.data.get('project_id') != project_id:
            raise HTTPException(409, 'Este identificador ya se usó para otro encargo.')
        if commit:db.commit()
        return _public(prior)
    total = db.scalar(select(func.count()).select_from(Item).where(Item.user_id==user.id,Item.source==SOURCE)) or 0
    pending = db.scalar(select(func.count()).select_from(Item).where(Item.user_id==user.id,Item.source==SOURCE,Item.status.in_(['pending','active']))) or 0
    since = (datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
    recent = db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id==user.id,Audit.action=='printing.generation_queued',Audit.created_at>=since)) or 0
    if total >= MAX_SAVED:raise HTTPException(409, 'Ya tienes 200 encargos guardados. Elimina alguno desde tus recursos para crear otro.')
    if pending >= MAX_PENDING:raise HTTPException(429, 'Ya hay tres organizadores pendientes. Espera o cancela uno.')
    if recent >= MAX_DAILY:raise HTTPException(429, 'Se han aceptado 20 encargos en 24 horas. Vuelve a intentarlo más tarde.')
    title = f"Organizador {params['columns']} × {params['rows']} · {params['width_mm']:g} × {params['depth_mm']:g} × {params['height_mm']:g} mm"
    row = Item(user_id=user.id, source=SOURCE, external_id=key, kind='model', area='printing',
               title=title, description='Organizador abierto paramétrico. Medidas en mm; revisar en el laminador antes de imprimir.',
               status='pending', data={'state':'queued','parameters':params,'attempt_count':0,
               'generator':GENERATOR_VERSION,'source_references':refs,'origin_user_prompt':True,'project_id':project_id,
               'provenance':'Geometría procedural original de Aros; sin modelos externos ni inferencia.',
               'printing_ready':False,'physical_print_verified':False})
    if not commit:
        # The caller owns rollback as well as commit. In particular, SQLite's
        # deferred BEGIN must not turn an outermost SAVEPOINT release into a
        # commit before a conversation has persisted its acceptance receipt.
        db.add(row);db.flush()
        audit(db,user,'printing.generation_queued',row.id);db.flush()
        return _public(row)
    try:
        with db.begin_nested():
            db.add(row);db.flush()
            audit(db,user,'printing.generation_queued',row.id);db.flush()
    except IntegrityError:
        prior=db.scalar(select(Item).where(Item.user_id==user.id,Item.source==SOURCE,Item.external_id==key))
        if not prior or prior.data.get('parameters')!=params or prior.data.get('source_references',[])!=refs or prior.data.get('project_id')!=project_id:
            raise HTTPException(409,'El encargo cambió; vuelve a cargarlo.') from None
        row=prior
    if commit:db.commit()
    return _public(row)


@router.get('/capabilities')
def capabilities(user:UserDep, db:DbDep):
    return {'engine':'TRELLIS.2','available':False,'status':'requires_gpu_worker',
            'detail':'La conversión de imágenes necesita un motor Linux con GPU NVIDIA; este VPS no lo tiene.',
            'requirements':{'system':'Linux','nvidia_vram_gb':24},'outputs':['GLB'],'printing_ready':False,
            'parametric':{'engine':GENERATOR_VERSION,'available':True,'worker_state':worker_state(db.get(WorkerHeartbeat,'worker')),
                          'outputs':['STL'],'templates':['organizer'],'units':'mm','requires_ai_consent':False,
                          'limits':{'width_mm':[20,180],'depth_mm':[20,180],'height_mm':[10,100],
                                    'wall_mm':[1.2,6],'base_mm':[1.2,8],'columns':[1,6],'rows':[1,6],
                                    'minimum_compartment_mm':5,'max_pending':MAX_PENDING,'max_daily':MAX_DAILY,
                                    'max_saved':MAX_SAVED,'max_stl_bytes':MAX_STL_BYTES}}}


@router.post('/jobs', status_code=202)
def create_generation(payload:GenerationIn, user:UserDep, db:DbDep):
    return queue_generation(db,user,payload.parameters,payload.idempotency_key,project_id=payload.project_id)


@router.get('/jobs')
def list_generations(user:UserDep, db:DbDep):
    from .source_access import visible_items_clause
    rows=db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source==SOURCE,visible_items_clause(db,user)).order_by(Item.created_at.desc(),Item.id.desc()).limit(MAX_SAVED))
    result=[]
    for row in rows:
        try:_references_current(db,user,(row.data or {}).get('source_references',[]))
        except HTTPException:continue
        result.append(_public(row))
    return {'items':result,'worker_state':worker_state(db.get(WorkerHeartbeat,'worker'))}


def _owned(db,user,job_id):
    from .source_access import visible_items_clause
    row=db.scalar(select(Item).options(defer(Item.blob)).where(Item.id==job_id,Item.user_id==user.id,Item.source==SOURCE,visible_items_clause(db,user)))
    if not row:raise HTTPException(404,'No se ha encontrado el encargo.')
    _references_current(db,user,(row.data or {}).get('source_references',[]))
    return row


@router.get('/jobs/{job_id}')
def get_generation(job_id:str, user:UserDep, db:DbDep):
    return _public(_owned(db,user,job_id))


@router.post('/jobs/{job_id}/cancel')
def cancel_generation(job_id:str, user:UserDep, db:DbDep):
    return cancel_job(db,user,job_id)


def cancel_job(db,user,job_id,*,commit=True):
    """Shared cancellation; fence an in-flight worker in the caller's transaction."""
    row=_owned(db,user,job_id)
    if row.status in ('pending','active'):
        data={**row.data,'state':'cancelled','finished_at':now(),'lease_id':None,'lease_expires_at':None}
        changed=db.execute(update(Item).where(Item.id==row.id,Item.user_id==user.id,Item.source==SOURCE,Item.version==row.version,Item.status.in_(['pending','active'])).values(status='cancelled',data=data,version=Item.version+1,updated_at=now()))
        if changed.rowcount!=1:raise HTTPException(409,'El encargo cambió. Vuelve a cargarlo.')
        audit(db,user,'printing.generation_cancelled',row.id)
        if commit:db.commit()
        else:db.flush()
        db.refresh(row)
    return _public(row)


def _verified_file(db,user,job_id):
    row=_owned(db,user,job_id)
    if row.status!='done' or row.data.get('state')!='verified':
        raise HTTPException(409,'El STL todavía no está verificado.')
    try:
        evidence=verify_stl(row.blob,row.data['parameters'])
        if evidence != row.data.get('evidence') or evidence['sha256'] != row.data.get('sha256'):
            raise ValueError('evidence mismatch')
    except (ValueError,TypeError,KeyError):
        raise HTTPException(409,'El archivo no supera la comprobación de integridad. No se ha descargado.') from None
    return row,evidence


@router.get('/jobs/{job_id}/file')
def download_generation(job_id:str, user:UserDep, db:DbDep):
    row,evidence=_verified_file(db,user,job_id)
    return Response(row.blob,media_type='model/stl',headers={'Content-Disposition':f'attachment; filename="organizador-{row.id}-mm.stl"',
                    'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','ETag':'"'+evidence['sha256']+'"'})


@router.get('/jobs/{job_id}/manifest')
def download_manifest(job_id:str, user:UserDep, db:DbDep):
    row,evidence=_verified_file(db,user,job_id)
    manifest={'job_id':row.id,'parameters':row.data['parameters'],'evidence':evidence,'created_at':row.created_at,
              'verified_at':row.data['finished_at'],'provenance':row.data['provenance'],
              'notes':['STL no codifica unidades: importar en milímetros.','Comprobación geométrica; impresión física y ajustes de laminado pendientes.']}
    return Response(json.dumps(manifest,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="organizador-{row.id}-informe.json"','Cache-Control':'private, no-store'})


def process_generation_batch(db):
    """Recover bounded, pure CPU work; one accepted job per invocation."""
    stale=list(db.scalars(select(Item).options(defer(Item.blob)).where(Item.source==SOURCE,Item.status=='active').order_by(Item.updated_at).limit(100)))
    for row in stale:
        data=row.data or {}
        try:expired=datetime.fromisoformat(data.get('lease_expires_at',''))<=datetime.now(timezone.utc)
        except (ValueError,TypeError):expired=True
        if not expired:continue
        exhausted=data.get('attempt_count',0)>=MAX_ATTEMPTS
        values={**data,'state':'failed' if exhausted else 'queued','lease_id':None,'lease_expires_at':None,
                'error':'La generación se interrumpió tres veces. Puedes crear otro encargo.' if exhausted else None}
        if exhausted:values['finished_at']=now()
        db.execute(update(Item).where(Item.id==row.id,Item.source==SOURCE,Item.version==row.version,Item.status=='active').values(status='cancelled' if exhausted else 'pending',data=values,version=Item.version+1,updated_at=now()))
    db.commit()
    row=db.scalar(select(Item).options(defer(Item.blob)).where(Item.source==SOURCE,Item.status=='pending').order_by(Item.created_at,Item.id).limit(1))
    if not row:return None
    ident,version=row.id,row.version
    data={**row.data,'state':'generating','lease_id':str(uuid4()),'started_at':now(),
          'lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=LEASE_SECONDS)).isoformat(),
          'attempt_count':int(row.data.get('attempt_count',0))+1,'error':None}
    claimed=db.execute(update(Item).where(Item.id==ident,Item.source==SOURCE,Item.status=='pending',Item.version==version).values(status='active',data=data,version=version+1,updated_at=now()))
    db.commit()
    if claimed.rowcount!=1:return None
    owned_version=version+1
    condition=(Item.id==ident,Item.source==SOURCE,Item.status=='active',Item.version==owned_version,Item.data['lease_id'].as_string()==data['lease_id'])
    started=time.monotonic()
    try:
        user=db.get(User,row.user_id)
        if not user:raise ValueError('owner missing')
        _references_current(db,user,data.get('source_references',[]));db.commit()
        blob,evidence=generate_organizer(data['parameters'])
        _references_current(db,user,data.get('source_references',[]))
        finished={**data,'state':'verified','evidence':evidence,'sha256':evidence['sha256'],
                  'filename':f'organizador-{ident}-mm.stl','mime':'model/stl','size':len(blob),
                  'finished_at':now(),'elapsed_ms':round((time.monotonic()-started)*1000,2),
                  'lease_id':None,'lease_expires_at':None}
        changed=db.execute(update(Item).where(*condition).values(status='done',blob=blob,data=finished,version=owned_version+1,updated_at=now()))
        if changed.rowcount==1:audit(db,user,'printing.generation_verified',ident)
        db.commit()
        return ident if changed.rowcount==1 else None
    except Exception:
        db.rollback()
        failed={**data,'state':'failed','finished_at':now(),'lease_id':None,'lease_expires_at':None,
                'error':'No se pudo verificar el organizador o sus fuentes. Revisa las medidas y crea otro encargo.'}
        db.execute(update(Item).where(*condition).values(status='cancelled',data=failed,version=owned_version+1,updated_at=now()))
        db.commit();return None


def process_generation_jobs():
    with SessionLocal() as db:
        return process_generation_batch(db)
