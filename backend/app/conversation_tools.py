"""Typed application adapters. Caller owns the transaction and execution lease.

These tools never select users, hosts, credentials, SQL or connector permissions.
Model/resource text cannot authorize a write: authorization comes from the original
human command, scoped by the conversation engine before this module is called.
"""
import copy,re,unicodedata
from datetime import date,datetime,timedelta,timezone
from uuid import UUID,uuid5,NAMESPACE_URL
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool,field_validator
from sqlalchemy import select,update
from sqlalchemy.orm import defer
from .database import Item,Connector,User,now
from .schemas import Area,ItemIn
from .source_access import visible_items_clause,resource_fingerprint,effect_fingerprint
from .agent_capabilities import CapabilityBlocked,revalidate_sources,digest
from .services import attention_for_item
from .security import audit

AREAS={'university','prisma','technology','printing','personal','server'}
KINDS={'note','task','reminder','event','project'}
PRIVATE_CONNECTORS={'google-application','_whatsapp-control','_canva-application','_printing-discovery'}

class StrictArgs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class EmptyArgs(StrictArgs):pass
class WeatherArgs(StrictArgs):
    refresh:StrictBool=False
class CalendarArgs(StrictArgs):
    start:str=Field(min_length=10,max_length=10,description='Fecha inicial incluida, YYYY-MM-DD. El intervalo debe contener entre 1 y 62 días.')
    end:str=Field(min_length=10,max_length=10,description='Fecha final EXCLUSIVA, YYYY-MM-DD, posterior a start y como máximo 62 días después. Para consultar un día, end debe ser el día siguiente a start.')
    zone:str=Field(default='Europe/Madrid',max_length=80,description='Zona horaria IANA válida; por defecto Europe/Madrid.')
    @field_validator('start','end')
    @classmethod
    def dates(cls,value):return date.fromisoformat(value).isoformat()

class CreateArgs(StrictArgs):
    kind:Literal['note','task','reminder','event','project']
    area:Area
    title:str=Field(min_length=1,max_length=240)
    description:str=Field(default='',max_length=12000)
    status:Literal['pending','active','done']='pending'
    priority:Literal['normal','high','urgent']='normal'
    due_at:str|None=Field(default=None,max_length=80)
    end_at:str|None=Field(default=None,max_length=80)
    all_day:StrictBool=False
    project_id:str|None=Field(default=None,min_length=1,max_length=100,description='Proyecto interno del mismo área, previamente leído y solicitado como destino. No admite referencias Canva ni proyectos inferidos.')
    operation_key:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.:-]+$')
    @field_validator('title')
    @classmethod
    def titles(cls,value):
        if not value.strip():raise ValueError('Título vacío')
        return value.strip()
    @field_validator('due_at','end_at')
    @classmethod
    def timestamps(cls,value):
        if value is None:return value
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('La fecha necesita zona horaria')
        return stamp.isoformat()

class Changes(StrictArgs):
    title:str|None=Field(default=None,min_length=1,max_length=240)
    description:str|None=Field(default=None,max_length=12000)
    status:Literal['pending','active','done']|None=None
    priority:Literal['normal','high','urgent']|None=None
    due_at:str|None=Field(default=None,max_length=80)
    end_at:str|None=Field(default=None,max_length=80)
    _title=field_validator('title')(classmethod(lambda cls,v:CreateArgs.titles(v) if v is not None else v))
    _dates=field_validator('due_at','end_at')(classmethod(lambda cls,v:CreateArgs.timestamps(v)))
class UpdateArgs(StrictArgs):
    id:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1)
    changes:Changes
class FavoriteArgs(StrictArgs):
    id:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1)
    favorite:StrictBool

from .printing_mesh import OrganizerParameters
class OrganizerArgs(StrictArgs):
    parameters:OrganizerParameters
    operation_key:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_.:-]+$')

TOOL_SCHEMAS={'weather.current':WeatherArgs,'calendar.list':CalendarArgs,
              'platform.status':EmptyArgs,'connections.status':EmptyArgs,
              'items.create':CreateArgs,'items.update':UpdateArgs,
              'printing.favorite':FavoriteArgs,'printing.organizer.create':OrganizerArgs}
WRITE_TOOLS={'items.create','items.update','printing.favorite','printing.organizer.create'}

def registry():
    return {name:{'parameters':schema.model_json_schema(),'writes':name in WRITE_TOOLS}
            for name,schema in TOOL_SCHEMAS.items()}

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))

def extra_creation_limit(prompt):
    """An explicit limit on additional objects is not permission to create one."""
    text=normalized(prompt or '')
    text=re.sub(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`','',text)
    pattern=r'\bno\s+(?:me\s+)?(?:crees|crear|anadas|anadir|agregues|guardes)\s+(?:mas|otros|otras|adicionales)\s+(?:elementos|recordatorios|notas|tareas|eventos|proyectos)(?=\s*(?:[.!?;,]|$))'
    return text,re.compile(pattern)

def human_intent(prompt,name):
    text=normalized(prompt or '').strip().lstrip('¿¡')
    text=re.sub(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`','',text)
    if name=='items.create':
        # Remove only a complete restriction on *additional* objects. A real
        # positive request must still match below; negating its object remains
        # a veto. Quoted text never supplies either permission or this exception.
        _,extra_pattern=extra_creation_limit(prompt)
        text=extra_pattern.sub('',text)
    suffix=r'(?:me|lo|la|los|las)?'
    verbs={'items.create':r'crea|crear|crees|guarda|guardar|guardes|anade|anadir|anadas|agrega|agregar|agregues|apunta|apuntar|apuntes|recuerda|recordar|recuerdes|programa|programar|programes|agenda|agendar|agendes|pon|poner|pongas',
           'items.update':r'actualiza|actualizar|actualices|modifica|modificar|modifiques|cambia|cambiar|cambies|edita|editar|edites|completa|completar|completes|marca|marcar|marques|termina|terminar|termines|corrige|corregir|corrijas',
           'printing.favorite':r'guarda|guardar|guardes|quita|quitar|quites|anade|anadir|anadas|marca|marcar|marques|pon|poner|pongas',
           'printing.organizer.create':r'crea|crear|crees|genera|generar|generes|disena|disenar|disenes'}[name]
    # A prohibition on modifying originals or external sending must not veto
    # an independently requested internal creation. Negations fence the tool's
    # own verb family; they never grant another family permission.
    if re.search(r'\b(no|sin)\s+(?:(?:me|lo|la|los|las)\s+)?(?:'+verbs+r')'+suffix+r'\b',text):return False
    if name=='printing.favorite' and not re.search(r'\bfavoritos?\b',text):return False
    if name=='printing.organizer.create' and not re.search(r'\borganizador(?:es)?\b',text):return False
    if name=='items.create' and re.search(r'\b(?:pon|poner|pongas)'+suffix+r'\b',text) and not re.search(r'\b(nota|notas|tarea|tareas|recordatorio|recordatorios|evento|eventos|proyecto|proyectos)\b',text):return False
    # A verb mentioned in a document/quotation is not an imperative request.
    start=r'(?:^|[.!?;]\s*|\by\s+)(?:(?:por favor|despues|luego|ademas|tambien|hola)[,]?\s+)*'
    request=r'(?:quiero|necesito|puedes|podrias|ayudame a|me puedes)\s+(?:que\s+)?(?:me\s+)?'
    return bool(re.search(start+r'(?:(?:'+request+r'))?(?:'+verbs+r')'+suffix+r'\b',text))

def observation(status,summary,data=None,artifacts=None,next_actions=None):
    result={'status':status,'summary':summary,'next_actions':next_actions or [],'artifacts':artifacts or []}
    if data is not None:result['data']=data
    return result

def allowed_areas(scope):
    areas=scope.get('areas') or ([scope['area']] if scope.get('area') else list(AREAS))
    if not isinstance(areas,list) or not set(areas)<=AREAS:raise CapabilityBlocked('scope_invalid','Ámbito no reconocido.')
    return set(areas)

def current_owner(db,user):
    owner=db.scalar(select(User).where(User.id==user.id).with_for_update().execution_options(populate_existing=True))
    if not owner or not owner.ai_consent:raise CapabilityBlocked('consent_revoked','El permiso de IA no está activo.')
    return owner

def read_item(db,user,ident,scope):
    item=db.scalar(select(Item).options(defer(Item.blob)).where(Item.id==ident,Item.user_id==user.id,Item.status.notin_(['archived','cancelled','canceled']),visible_items_clause(db,user)).with_for_update().execution_options(populate_existing=True))
    if not item or item.area not in allowed_areas(scope) or (scope.get('item_ids') and item.id not in scope['item_ids']):raise CapabilityBlocked('resource_unavailable','El recurso no está disponible en el ámbito autorizado.')
    return item

def references(db,user,scope,authorization):
    raw=authorization.get('read_sources') or []
    if not isinstance(raw,list) or len(raw)>40:raise CapabilityBlocked('source_limit','Máximo cuarenta fuentes leídas.')
    if any(not isinstance(ref,dict) or not isinstance(ref.get('id'),str) or not isinstance(ref.get('version'),int) or not isinstance(ref.get('content_hash'),str) for ref in raw):raise CapabilityBlocked('source_invalid','Referencia de lectura inválida.')
    refs=list({ref['id']:{'type':'source','id':ref['id'],'version':ref['version'],'content_hash':ref['content_hash']} for ref in raw}.values())
    if scope.get('item_ids') and any(ref['id'] not in scope['item_ids'] for ref in refs):raise CapabilityBlocked('scope_denied','Una fuente está fuera de la selección autorizada.')
    # Existing capability validates one area at a time; never broaden its scope.
    rows={r.id:r for r in db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.id.in_([r['id'] for r in refs])))}
    for area in allowed_areas(scope):
        group=[ref for ref in refs if ref['id'] in rows and rows[ref['id']].area==area]
        if group:revalidate_sources(db,user,{'area':area},group)
    if any(ref['id'] not in rows or rows[ref['id']].area not in allowed_areas(scope) for ref in refs):raise CapabilityBlocked('source_unavailable','Una fuente no está autorizada.')
    return refs

def receipt(item):
    return {'id':item.id,'version':item.version,'title':item.title,'area':item.area,'kind':item.kind,
            'content_hash':resource_fingerprint(item),'effect_hash':effect_fingerprint(item),'source_references':copy.deepcopy((item.data or {}).get('source_references',[]))}

def write_authorized(db,user,scope,authorization,name):
    current_owner(db,user)
    if authorization.get('authorize_internal_write') is not True or not human_intent(authorization.get('prompt',''),name):raise CapabilityBlocked('write_not_authorized','El mensaje original no autoriza esta escritura interna.')
    try:UUID(authorization['job_id'])
    except (KeyError,ValueError,TypeError):raise CapabilityBlocked('authorization_invalid','El trabajo no tiene una autorización válida.') from None
    return references(db,user,scope,authorization)

def calendar_metadata(kind,due_at,end_at,all_day=False):
    if kind!='event':
        if end_at or all_day:raise CapabilityBlocked('invalid_event','Solo los eventos admiten fin o día completo.')
        return {}
    if not due_at:raise CapabilityBlocked('missing_date','Un evento necesita fecha y hora con zona.')
    start=datetime.fromisoformat(due_at.replace('Z','+00:00'))
    if end_at and all_day and len(end_at)==10:
        finish=datetime.combine(date.fromisoformat(end_at),datetime.min.time(),start.tzinfo)
    else:finish=datetime.fromisoformat(end_at.replace('Z','+00:00')) if end_at else start+timedelta(days=1 if all_day else 0,hours=0 if all_day else 1)
    if finish<=start:raise CapabilityBlocked('invalid_event','El fin debe ser posterior al inicio.')
    return {'all_day':all_day,'start_date':start.date().isoformat(),'end_at':finish.date().isoformat() if all_day else finish.astimezone(timezone.utc).isoformat()}

def create_item(db,user,args,scope,authorization):
    refs=write_authorized(db,user,scope,authorization,'items.create')
    if args.area not in allowed_areas(scope):raise CapabilityBlocked('scope_denied','La nueva nota o tarea pertenece a otra área.')
    metadata=calendar_metadata(args.kind,args.due_at,args.end_at,args.all_day)
    if args.project_id:
        if scope.get('project_id') and args.project_id!=scope['project_id']:
            raise CapabilityBlocked('scope_denied','El destino no coincide con el proyecto de esta conversación.')
        project=read_item(db,user,args.project_id,scope)
        if project.kind!='project' or project.source not in ['manual','agent-conversation'] or project.area!=args.area:
            raise CapabilityBlocked('project_unavailable','El destino debe ser un proyecto interno disponible del mismo área; una referencia Canva no es un proyecto.')
        if project.id not in set(authorization.get('read_ids',[ref['id'] for ref in refs])):
            raise CapabilityBlocked('target_not_read','Lee el proyecto y su versión actual antes de vincular el resultado.')
        destination=normalized(authorization.get('prompt',''))
        destination=re.sub(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`','',destination)
        forbidden=re.search(r'\b(?:no|sin)\s+(?:lo\s+)?(?:vincul\w*|asoci\w*|enlaz\w*)',destination)
        title=normalized(project.title or '')
        if forbidden or not (re.search(r'\bproyectos?\b',destination) or (len(title)>3 and title in destination)):
            raise CapabilityBlocked('write_not_authorized','El mensaje no solicita vincular este resultado a un proyecto. Concreta el destino antes de añadir la relación.')
    elif scope.get('project_id'):
        raise CapabilityBlocked('scope_denied','Esta conversación pertenece a un proyecto: indica su project_id al guardar el resultado.')
    payload=ItemIn.model_validate(args.model_dump(exclude={'operation_key','end_at','all_day','project_id'})).model_dump()
    payload['data']={**metadata,'origin_user_prompt':True,'execution_id':authorization['job_id'],'source_references':refs}
    if args.project_id:payload['data']['project_id']=args.project_id
    # The operation key is supplied by the planner; the job namespace fixes owner
    # and repeat handling. Same key with changed content cannot overwrite.
    external=authorization['job_id']+':'+args.operation_key
    ident=str(uuid5(NAMESPACE_URL,'aros-conversation:'+user.id+':'+external));hashed=digest(payload)
    prior=db.scalar(select(Item).where(Item.id==ident).with_for_update().execution_options(populate_existing=True))
    if prior:
        if prior.user_id!=user.id or prior.source!='agent-conversation' or prior.external_id!=external or (prior.data or {}).get('creation_hash')!=hashed:raise CapabilityBlocked('operation_conflict','La operación ya creó un recurso diferente.')
        if prior.status in ['archived','cancelled','canceled']:raise CapabilityBlocked('operation_archived','El resultado anterior fue archivado; no se recrea.')
        if any(getattr(prior,field)!=value for field,value in payload.items() if field!='data') or any((prior.data or {}).get(field)!=value for field,value in payload['data'].items()):raise CapabilityBlocked('operation_changed','El resultado anterior cambió; no se recrea ni se sobrescribe.')
        return observation('success','La creación ya estaba registrada.',artifacts=[receipt(prior)],data={'id':prior.id,'version':prior.version,'replayed':True})
    restriction_text,extra_pattern=extra_creation_limit(authorization.get('prompt',''))
    if extra_pattern.search(restriction_text):
        count_match=re.search(r'\b(?:crea|crear|crees|anade|anadir|anadas|agrega|agregar|agregues|guarda|guardar|guardes)\s+(?:me\s+)?(dos|tres|cuatro|\d+)\s+(?:elementos|recordatorios|notas|tareas|eventos|proyectos)\b',restriction_text)
        count_word=count_match.group(1) if count_match else '1'
        limit={'dos':2,'tres':3,'cuatro':4}.get(count_word)
        if limit is None:limit=int(count_word)
        if not 1<=limit<=4:
            raise CapabilityBlocked('artifact_limit','Concreta un máximo de cuatro elementos para este turno.')
        created=list(db.scalars(select(Item.id).where(Item.user_id==user.id,Item.source=='agent-conversation',Item.external_id.startswith(authorization['job_id']+':')).limit(limit)))
        if len(created)>=limit:
            raise CapabilityBlocked('artifact_limit',f'El mensaje autoriza {limit} elemento(s); no se crearán elementos adicionales.')
    payload['data']['creation_hash']=hashed
    item=Item(id=ident,user_id=user.id,source='agent-conversation',external_id=external,**payload);db.add(item);db.flush()
    attention_for_item(db,user,item,reopen=True);audit(db,user,'conversation.item_created',item.id)
    return observation('success','Recurso interno creado; debe verificarse antes de afirmar éxito.',artifacts=[receipt(item)],data={'id':item.id,'version':item.version})

def update_item(db,user,args,scope,authorization):
    refs=write_authorized(db,user,scope,authorization,'items.update');item=read_item(db,user,args.id,scope)
    if item.id not in set(authorization.get('read_ids',[ref['id'] for ref in refs])):raise CapabilityBlocked('target_not_read','Lee el recurso actual antes de modificarlo.')
    if item.source not in ['manual','agent-conversation'] or item.kind not in KINDS:raise CapabilityBlocked('original_protected','Este original no se modifica; prepara una copia interna autorizada.')
    if item.version!=args.version:raise CapabilityBlocked('version_conflict','El recurso cambió; vuelve a leer su versión.')
    changes=args.changes.model_dump(exclude_unset=True)
    if not changes:raise CapabilityBlocked('empty_change','Indica un cambio concreto.')
    if any(changes.get(field) is None for field in ['title','description','status','priority'] if field in changes):raise CapabilityBlocked('invalid_change','Ese campo no puede quedar vacío.')
    if all((item.data or {}).get('end_at')==value if field=='end_at' else getattr(item,field)==value for field,value in changes.items()):
        return observation('success','El recurso ya tenía exactamente los valores solicitados; no se ha modificado.',artifacts=[{**receipt(item),'effect':'unchanged'}],data={'id':item.id,'version':item.version,'changed':False})
    data=copy.deepcopy(item.data or {});end=changes.pop('end_at',None)
    if 'due_at' in changes or 'end_at' in args.changes.model_fields_set:
        metadata=calendar_metadata(item.kind,changes.get('due_at',item.due_at),end or data.get('end_at'),bool(data.get('all_day')))
        data.update(metadata)
    # Preserve existing dependencies and validate them before updating a draft.
    previous=data.get('source_references') or []
    # Choosing the derived record does not select its ancestors for new reads.
    # It does permit revalidating its already-recorded lineage without dropping
    # provenance. This validation scope is never sent to the planner or used by
    # tools; areas, ownership, availability and version fences stay unchanged.
    dependency_scope={**scope}
    if scope.get('item_ids'):
        dependency_scope['item_ids']=list(dict.fromkeys([*scope['item_ids'],
            *[ref['id'] for ref in previous if isinstance(ref,dict) and isinstance(ref.get('id'),str)]]))
    previous_refs=references(db,user,dependency_scope,{**authorization,'read_sources':previous}) if previous else []
    merged={ref['id']:ref for ref in [*previous_refs,*refs] if ref['id']!=item.id}
    if len(merged)>40:raise CapabilityBlocked('source_limit','Máximo cuarenta dependencias por recurso.')
    data.update(source_references=list(merged.values()),origin_user_prompt=True,last_execution_id=authorization['job_id'])
    candidate=ItemIn.model_validate({'kind':item.kind,'area':item.area,'title':item.title,'description':item.description,'status':item.status,'priority':item.priority,'due_at':item.due_at,'data':data,**changes})
    values={field:getattr(candidate,field) for field in changes};values.update(data=data,version=Item.version+1,updated_at=now())
    if merged:values['source']='agent-conversation'
    changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==args.version).values(**values))
    if changed.rowcount!=1:raise CapabilityBlocked('version_conflict','El recurso cambió durante la edición.')
    db.expire(item);db.refresh(item);attention_for_item(db,user,item,reopen=True);audit(db,user,'conversation.item_updated',item.id)
    return observation('success','Cambio interno guardado; debe verificarse.',artifacts=[receipt(item)],data={'id':item.id,'version':item.version})

def favorite(db,user,args,scope,authorization):
    refs=write_authorized(db,user,scope,authorization,'printing.favorite');item=read_item(db,user,args.id,scope)
    if item.id not in set(authorization.get('read_ids',[ref['id'] for ref in refs])):raise CapabilityBlocked('target_not_read','Lee el modelo actual antes de cambiar su favorito.')
    from .printing import SOURCE
    if item.area!='printing' or item.kind!='model' or item.source!=SOURCE:raise CapabilityBlocked('model_unavailable','El modelo no pertenece a la colección de impresión.')
    if item.version!=args.version:raise CapabilityBlocked('version_conflict','El modelo cambió; vuelve a leerlo.')
    if bool((item.data or {}).get('favorite',False))==args.favorite:
        return observation('success','El favorito ya estaba en el estado solicitado; no se ha modificado.',artifacts=[{**receipt(item),'effect':'unchanged'}],data={'id':item.id,'version':item.version,'favorite':args.favorite,'changed':False})
    changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==args.version).values(data={**(item.data or {}),'favorite':args.favorite},version=Item.version+1,updated_at=now()))
    if changed.rowcount!=1:raise CapabilityBlocked('version_conflict','El modelo cambió durante la operación.')
    db.expire(item);db.refresh(item);audit(db,user,'conversation.printing_favorite',item.id)
    return observation('success','Preferencia de favorito guardada; no se imprimió ni descargó el modelo.',artifacts=[receipt(item)],data={'id':item.id,'version':item.version,'favorite':args.favorite})


def organizer(db,user,args,scope,authorization):
    refs=write_authorized(db,user,scope,authorization,'printing.organizer.create')
    if 'printing' not in allowed_areas(scope):raise CapabilityBlocked('scope_denied','El organizador pertenece al área de impresión 3D.')
    if len(refs)>20:raise CapabilityBlocked('source_limit','La generación admite hasta veinte fuentes; concreta la selección.')
    prompt=normalized(authorization.get('prompt',''))
    prompt=re.sub(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`','',prompt)
    defaults=bool(re.search(r'\b(?:medidas|parametros|valores|dimensiones)\s+(?:por defecto|predeterminad\w*|estandar)',prompt))
    dimensions=bool(re.search(r'\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?',prompt))
    measured=all(re.search(pattern,prompt) for pattern in [r'\b(?:ancho|anchura)\b',r'\b(?:fondo|profundidad)\b',r'\b(?:alto|altura)\b',r'\bpared\w*\b',r'\bbase\b']) and bool(re.search(r'\d',prompt))
    documented=bool(authorization.get('read_ids') and re.search(r'\b(?:medidas|dimensiones)\b.*\b(?:documento|recurso|ficha|material)\b',prompt))
    if not defaults and not ((dimensions or measured) and (re.search(r'\bpared\w*\b',prompt) and re.search(r'\bbase\b',prompt))) and not documented:
        raise CapabilityBlocked('missing_dimensions','Concreta ancho, fondo, altura, pared y base en mm, y filas/columnas; o autoriza expresamente los valores por defecto.')
    if scope.get('project_id') and scope['project_id'] not in authorization.get('read_ids',[]):raise CapabilityBlocked('target_not_read','Lee la ficha del proyecto antes de aceptar el encargo.')
    from .printing_generation import queue_generation
    key=str(uuid5(NAMESPACE_URL,'aros-organizer:'+authorization['job_id']+':'+args.operation_key))
    job=queue_generation(db,user,args.parameters,key,commit=False,source_references=refs,project_id=scope.get('project_id'))
    return observation('warning','Encargo de organizador aceptado y guardado. El STL aún debe generarse y verificarse; no se ha impreso.',data={'accepted':True,'generation_job':job},next_actions=['Consulta el encargo en Impresión 3D; sólo un estado verified acredita el STL.'])

def weather(db,user,args):
    row=db.scalar(select(Connector).where(Connector.id=='weather',Connector.user_id==user.id).with_for_update().execution_options(populate_existing=True))
    if not row or row.status in ['disconnected','revoked'] or not (row.sync_details or {}).get('location'):return observation('warning','El tiempo no está conectado a una ciudad.',data={'configured':False,'stale':True},next_actions=['Configurar una ciudad en Tiempo'])
    data=row.sync_details or {};forecast=data.get('forecast');fresh=False
    try:fresh=bool(row.last_sync and datetime.fromisoformat(row.last_sync.replace('Z','+00:00'))>=datetime.now(timezone.utc)-timedelta(minutes=30))
    except (ValueError,TypeError):pass
    if args.refresh and row.status not in ['paused','syncing']:
        from .weather import refresh
        try:
            forecast=refresh(db,user,commit=False)
            current_owner(db,user)
            row.sync_details={'location':data['location'],'forecast':forecast};row.last_sync=now();row.status='connected';row.error=None;fresh=True
        except HTTPException as exc:
            if exc.status_code not in [502,503]:raise
            return observation('warning','No se pudo actualizar el tiempo. Se conserva la última lectura, que no acredita condiciones actuales.',data={'location':{'label':str(data['location'].get('label',''))[:120]},'forecast':{key:forecast[key] for key in ['temperature','feels_like','description','wind_kmh','maximum','minimum','rain_probability','observed_at','timezone','provider'] if isinstance(forecast,dict) and key in forecast},'last_sync':row.last_sync,'stale':True,'refresh_failed':True})
    forecast_keys=['temperature','feels_like','description','wind_kmh','maximum','minimum','rain_probability','observed_at','timezone','provider']
    clean={key:forecast[key] for key in forecast_keys if isinstance(forecast,dict) and key in forecast}
    return observation('success' if fresh and row.status not in ['paused','error'] else 'warning','Lectura del tiempo disponible.' if fresh else 'Última lectura guardada; no acredita tiempo actual.',data={'location':{'label':str(data['location'].get('label',''))[:120]},'forecast':clean,'last_sync':row.last_sync,'status':row.status,'stale':not fresh or row.status in ['paused','error']},next_actions=['weather.current con refresh=true para solicitar condiciones actuales'] if not fresh and row.status not in ['paused','syncing'] else [])

def platform(db,user):
    from .server import server_health
    connector=db.scalar(select(Connector).where(Connector.id=='server',Connector.user_id==user.id).with_for_update().execution_options(populate_existing=True))
    if connector and connector.status in ['disconnected','revoked']:return observation('warning','La conexión del servidor no está autorizada.',data={'status':connector.status,'available':False})
    paused=bool(connector and connector.status=='paused')
    raw=(connector.sync_details or {}) if paused else server_health();allowed=['scope','cpu_percent','ram_used','ram_total','disk_used','disk_total','uptime_seconds','demo']
    data={key:raw[key] for key in allowed if key in raw}
    data['services']=[{'name':str(entry.get('name',''))[:100],'status':str(entry.get('status',''))[:40]} for entry in raw.get('services',[])[:12] if isinstance(entry,dict)]
    data['checked_at']=connector.last_sync if paused else now();data['actions_enabled']=False;data['stale']=paused
    return observation('warning' if paused else 'success','Último estado guardado; la conexión está pausada.' if paused else 'Estado de lectura del servicio consultado; no se ejecutó ninguna acción.',data=data)

def connections(db,user):
    data=[{'id':c.id,'name':c.name,'status':c.status,'last_sync':c.last_sync,'has_authorization':bool(c.encrypted_tokens),'scopes':[str(s)[:100] for s in (c.scopes or [])[:12]]} for c in db.scalars(select(Connector).where(Connector.user_id==user.id,Connector.id.notin_(PRIVATE_CONNECTORS)).order_by(Connector.id).limit(30))]
    return observation('success','Estado registrado de las conexiones; no valida permisos nuevos.',data={'connections':data})

def execute_tool(db,user,name,args,scope,authorization):
    """No commit/rollback: engine atomically persists effect and receipt together."""
    if name not in TOOL_SCHEMAS:raise CapabilityBlocked('tool_unknown','Herramienta no disponible.')
    parsed=args if isinstance(args,TOOL_SCHEMAS[name]) else TOOL_SCHEMAS[name].model_validate(args)
    current_owner(db,user);allowed_areas(scope)
    if name=='items.create':return create_item(db,user,parsed,scope,authorization)
    if name=='items.update':return update_item(db,user,parsed,scope,authorization)
    if name=='printing.favorite':return favorite(db,user,parsed,scope,authorization)
    if name=='printing.organizer.create':return organizer(db,user,parsed,scope,authorization)
    if name=='weather.current':return weather(db,user,parsed)
    if name=='platform.status':return platform(db,user)
    if name=='connections.status':return connections(db,user)
    from .calendar import calendar_range
    start,end=date.fromisoformat(parsed.start),date.fromisoformat(parsed.end)
    if not 1<=(end-start).days<=62:
        raise CapabilityBlocked('calendar_range_invalid','La fecha final es exclusiva y debe estar entre 1 y 62 días después de la inicial. Para consultar un día, usa como fecha final el día siguiente.')
    try:result=calendar_range(user,db,start,end,parsed.zone)
    except HTTPException as exc:
        if exc.status_code!=422:raise
        raise CapabilityBlocked('calendar_timezone_invalid','Zona horaria desconocida. Usa una zona IANA válida, por ejemplo Europe/Madrid.') from None
    ids=[event['id'] for event in result['events']]
    visible=set(db.scalars(select(Item.id).where(Item.user_id==user.id,Item.id.in_(ids),visible_items_clause(db,user)))) if ids else set()
    events=[event for event in result['events'] if event['area'] in allowed_areas(scope) and event['id'] in visible]
    if scope.get('item_ids'):events=[event for event in events if event['id'] in scope['item_ids']]
    result.update(events=events[:100],truncated=len(events)>100,event_count=len(events))
    return observation('success','Agenda guardada consultada; otras agendas pueden no estar sincronizadas.',data=result)
