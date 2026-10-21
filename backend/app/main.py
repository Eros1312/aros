from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, deque
import io, json, logging, secrets, time
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form, Query, Header
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, delete, update, or_
from pypdf import PdfReader
from .config import settings
from sqlalchemy.orm import defer
from .database import Base, engine, get_db, User, Session, Item, Inbox, Connector, Confirmation, Chat, Automation, Run, Audit, now
from .schemas import LoginIn, ItemIn, ItemPatch, ItemOut, InboxPatch, ConfirmationIn, ChatIn, AutomationIn, ConsentIn
from .security import current_user, csrf, verify_password, hash_password, issue_session, digest, payload_hash, expiry, consume_confirmation, audit
from .seed import setup_user
from .services import owned_item, item_dict, inbox_dict, dashboard, attention_for_item, validate_transition, wake_inbox
from .connectors import oauth_start, oauth_callback, sync_connector, decode_tokens
from .server import server_health, server_action
from .ai import answer, normalized
from .openclaw import router as agent_router, runs as agent_runs
from .calendar import router as calendar_router
from .model_preferences import router as model_router
from .channels import router as channel_router
from .openclaw_tools import router as tool_router
from .google_setup import router as google_router
from .workspace import router as workspace_router
from .printing_generation import router as printing_generation_router
from .github_releases import router as github_releases_router
from .source_access import visible_items_clause
logger=logging.getLogger('aros')
UserDep=Annotated[User,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]

@asynccontextmanager
async def lifespan(app):
    settings.validate_production()
    if settings.app_env != 'production': Base.metadata.create_all(engine)
    from .database import SessionLocal
    with SessionLocal() as db: setup_user(db)
    yield

app=FastAPI(title='Aros API',version='0.1.0',lifespan=lifespan,dependencies=[Depends(csrf)])
app.include_router(calendar_router)
app.include_router(model_router)
app.include_router(channel_router)
app.include_router(tool_router)
app.include_router(google_router)
app.include_router(workspace_router)
app.include_router(printing_generation_router)
app.include_router(github_releases_router)
app.add_middleware(CORSMiddleware,allow_origins=[settings.public_origin],allow_credentials=True,allow_methods=['GET','POST','PATCH','DELETE'],allow_headers=['Content-Type','Authorization','X-Aros-Client','X-Confirmation'])
rate_buckets=defaultdict(deque)
@app.middleware('http')
async def protection(request,call_next):
    request_id=secrets.token_hex(8)
    if request.url.path.startswith('/api/'):
        key=(request.client.host if request.client else 'local',request.url.path.startswith('/api/v1/auth/'))
        bucket=rate_buckets[key]; limit=20 if key[1] else 600; tick=time.monotonic()
        while bucket and bucket[0]<tick-60: bucket.popleft()
        if len(bucket)>=limit: return JSONResponse({'detail':'Demasiadas peticiones. Espera un minuto.'},429)
        bucket.append(tick)
        if len(rate_buckets)>20000: rate_buckets.clear()
        length=request.headers.get('content-length')
        if length:
            try:
                limit=31_000_000 if request.url.path in ['/api/v1/connectors/university/import','/api/v1/prisma/import'] else 11_000_000
                if int(length)>limit: return JSONResponse({'detail':'El archivo supera el límite de subida'},413)
            except ValueError: return JSONResponse({'detail':'Tamaño de petición inválido'},400)
    start=time.monotonic()
    response=await call_next(request)
    response.headers['X-Request-ID']=request_id
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Permissions-Policy']='camera=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://cdn-learn.adafruit.com; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
        logger.info(json.dumps({'trace_id':request_id,'method':request.method,'path':request.url.path,'status':response.status_code,'ms':round((time.monotonic()-start)*1000)}))
    return response

@app.get('/api/v1/health')
def health():
    with engine.connect() as connection:
        from sqlalchemy import text
        connection.execute(text('SELECT 1'))
    return {'status':'ok','version':'0.1.0','demo':settings.demo_mode}
@app.get('/api/v1/config')
def config(): return {'demo':settings.demo_mode,'google_configured':bool(settings.google_client_id and settings.google_client_secret),'ai_configured':bool(settings.agent_configured or settings.openai_api_key),'agent_configured':settings.agent_configured,'ai_mode':'openclaw' if settings.agent_configured else 'external' if settings.openai_api_key else 'local'}
@app.post('/api/v1/auth/login')
def login(payload:LoginIn,response:Response,db:DbDep):
    user=db.scalar(select(User).where(User.email==payload.email))
    if not user or not verify_password(payload.password,user.password_hash): raise HTTPException(401,'Email o contraseña incorrectos')
    audit(db,user,'session.created'); return issue_session(db,user,response)
@app.post('/api/v1/auth/demo')
def demo_login(response:Response,db:DbDep):
    if not settings.demo_mode or settings.app_env=='production': raise HTTPException(403,'La demostración está desactivada')
    user=db.scalar(select(User).where(User.email==settings.admin_email))
    return issue_session(db,user,response)
@app.post('/api/v1/auth/logout')
def logout(request:Request,response:Response,user:UserDep,db:DbDep):
    auth=request.headers.get('authorization','')
    token=auth[7:] if auth.startswith('Bearer ') else request.cookies.get('aros_session','')
    db.execute(delete(Session).where(Session.token_hash==digest(token),Session.user_id==user.id)); db.commit()
    response.delete_cookie('aros_session',path='/'); return {'ok':True}
@app.post('/api/v1/auth/renew')
def renew(request:Request,response:Response,user:UserDep,db:DbDep):
    auth=request.headers.get('authorization',''); token=auth[7:] if auth.startswith('Bearer ') else request.cookies.get('aros_session','')
    db.execute(delete(Session).where(Session.token_hash==digest(token),Session.user_id==user.id))
    return issue_session(db,user,response)
@app.get('/api/v1/me')
def me(user:UserDep): return {'id':user.id,'email':user.email,'ai_consent':user.ai_consent,'demo':settings.demo_mode}
@app.patch('/api/v1/me')
def consent(payload:ConsentIn,user:UserDep,db:DbDep):
    user.ai_consent=payload.ai_consent; audit(db,user,'privacy.ai_consent',str(payload.ai_consent)); db.commit()
    if not user.ai_consent and user.email==settings.admin_email:
        from .channels import request_whatsapp_shutdown
        request_whatsapp_shutdown(db,user)
    return {'ai_consent':user.ai_consent}
@app.get('/api/v1/dashboard')
def home(user:UserDep,db:DbDep): return dashboard(db,user)
@app.get('/api/v1/items',response_model=list[ItemOut])
def items(user:UserDep,db:DbDep,area:str|None=None,kind:str|None=None,status:str|None=None,q:str='',limit:int=Query(ge=1,le=1000,default=300)):
    query=select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,visible_items_clause(db,user))
    if area: query=query.where(Item.area==area)
    if kind: query=query.where(Item.kind==kind)
    if status: query=query.where(Item.status==status)
    result=list(db.scalars(query.order_by(Item.updated_at.desc(),Item.id).limit(limit)))
    if q: result=[i for i in result if normalized(q) in normalized(i.title+' '+i.description+' '+i.area+' '+i.kind)]
    return result
@app.get('/api/v1/items/{item_id}',response_model=ItemOut)
def get_item(item_id:str,user:UserDep,db:DbDep): return owned_item(db,user,item_id)
@app.post('/api/v1/items',response_model=ItemOut,status_code=201)
def create(payload:ItemIn,user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    if payload.kind=='event': consume_confirmation(db,user,'event.create',payload.model_dump(),x_confirmation)
    item=Item(user_id=user.id,**payload.model_dump()); db.add(item); db.flush()
    attention_for_item(db,user,item); audit(db,user,'item.created',item.id); db.commit(); db.refresh(item); return item
@app.patch('/api/v1/items/{item_id}',response_model=ItemOut)
def patch(item_id:str,payload:ItemPatch,user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    item=owned_item(db,user,item_id)
    if item.kind=='event' or payload.kind=='event': consume_confirmation(db,user,'event.update',{'id':item_id,**payload.model_dump()},x_confirmation)
    return validate_transition(db,user,item,payload)
@app.delete('/api/v1/items/{item_id}')
def remove(item_id:str,user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    item=owned_item(db,user,item_id); consume_confirmation(db,user,'item.delete',{'id':item_id},x_confirmation)
    db.execute(delete(Inbox).where(Inbox.user_id==user.id,Inbox.item_id==item_id))
    audit(db,user,'item.deleted',item.id); db.delete(item); db.commit(); return {'ok':True}
@app.post('/api/v1/confirmations')
def confirmation(payload:ConfirmationIn,user:UserDep,db:DbDep):
    token=secrets.token_urlsafe(32)
    canonical=payload.payload
    try:
        if payload.action=='event.create': canonical=ItemIn.model_validate(canonical).model_dump()
        elif payload.action=='event.update': canonical={'id':canonical['id'],**ItemPatch.model_validate({k:v for k,v in canonical.items() if k!='id'}).model_dump()}
    except (ValueError,KeyError): raise HTTPException(422,'Revisa los datos del evento')
    db.add(Confirmation(id=token,user_id=user.id,action=payload.action,payload_hash=payload_hash(canonical),expires_at=expiry(5))); db.commit()
    return {'token':token,'expires_in':300}
@app.get('/api/v1/inbox')
def inbox(user:UserDep,db:DbDep,state:str|None=None):
    wake_inbox(db,user); query=select(Inbox).where(Inbox.user_id==user.id)
    visible=select(Item.id).where(Item.user_id==user.id,visible_items_clause(db,user))
    from sqlalchemy import or_
    query=query.where(or_(Inbox.item_id.is_(None),Inbox.item_id.in_(visible)))
    if state: query=query.where(Inbox.state==state)
    return [inbox_dict(i) for i in db.scalars(query.order_by(Inbox.created_at.desc()))]
@app.patch('/api/v1/inbox/{inbox_id}')
def inbox_update(inbox_id:str,payload:InboxPatch,user:UserDep,db:DbDep):
    item=db.scalar(select(Inbox).where(Inbox.id==inbox_id,Inbox.user_id==user.id))
    if not item: raise HTTPException(404,'No se ha encontrado el aviso')
    if item.item_id:owned_item(db,user,item.item_id)
    if payload.state=='snoozed' and (not payload.snoozed_until or payload.snoozed_until.tzinfo is None or payload.snoozed_until<=datetime.now(timezone.utc)): raise HTTPException(422,'Indica una fecha futura con zona horaria')
    item.state=payload.state; item.snoozed_until=payload.snoozed_until.isoformat() if payload.state=='snoozed' else None
    audit(db,user,'inbox.'+payload.state,item.id); db.commit(); return inbox_dict(item)
@app.post('/api/v1/inbox/{inbox_id}/task',response_model=ItemOut)
def inbox_task(inbox_id:str,user:UserDep,db:DbDep):
    alert=db.scalar(select(Inbox).where(Inbox.id==inbox_id,Inbox.user_id==user.id))
    if not alert: raise HTTPException(404,'Aviso no encontrado')
    if alert.item_id:
        original=owned_item(db,user,alert.item_id)
        if original.kind=='task': return original
    data={}
    if alert.item_id:
        from .source_access import resource_fingerprint
        data['source_references']=[{'id':original.id,'version':original.version,'content_hash':resource_fingerprint(original)}]
    item=Item(user_id=user.id,kind='task',area=alert.area,title=alert.title,description=alert.reason,data=data)
    db.add(item); db.flush(); alert.state='resolved'; audit(db,user,'inbox.converted_to_task',item.id); db.commit(); return item
@app.post('/api/v1/documents/upload',response_model=ItemOut,status_code=201)
def upload(user:UserDep,db:DbDep,file:UploadFile=File(...),area:str=Form('personal'),project_id:str|None=Form(None)):
    if area not in ['university','prisma','technology','printing','server','personal']: raise HTTPException(422,'Área inválida')
    if project_id:
        from .conversation_engine import project_for_context
        project_for_context(db,user,project_id,area)
    content=file.file.read(10_000_001)
    if len(content)>10_000_000: raise HTTPException(413,'El archivo supera 10 MB')
    name=Path(file.filename or 'Archivo').name[:200]; extension=Path(name).suffix.lower(); text=''
    allowed={'.pdf':'application/pdf','.txt':'text/plain','.md':'text/markdown','.stl':'application/octet-stream','.3mf':'application/octet-stream','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
    if extension not in allowed: raise HTTPException(422,'Admite PDF, texto, imágenes, STL y 3MF')
    if extension in ['.txt','.md']:
        try: text=content.decode('utf-8')[:150000]
        except UnicodeDecodeError: raise HTTPException(422,'El texto debe estar codificado en UTF-8')
    if extension=='.pdf':
        try:
            reader=PdfReader(io.BytesIO(content))
            if reader.is_encrypted: raise ValueError('encrypted')
            text='\n'.join((p.extract_text() or '')[:10000] for p in reader.pages[:40])[:150000]
        except Exception: raise HTTPException(422,'No se pudo leer el PDF. Comprueba que sea válido y no esté cifrado.')
    item=Item(user_id=user.id,kind='model' if extension in ['.stl','.3mf'] else 'document',area=area,title=name,description=text,source='upload',status='active',blob=content,data={'filename':name,'mime':allowed[extension],'size':len(content),'extracted':bool(text),'partial':extension=='.pdf',**({'project_id':project_id} if project_id else {})})
    db.add(item); db.flush(); audit(db,user,'document.uploaded',item.id); db.commit(); return item
@app.get('/api/v1/documents/{item_id}/download')
def download(item_id:str,user:UserDep,db:DbDep):
    item=owned_item(db,user,item_id)
    if item.source=='printing-generation':
        from .printing_generation import download_generation
        return download_generation(item_id,user,db)
    if item.blob is None: raise HTTPException(404,'Este recurso no contiene un archivo local')
    from urllib.parse import quote
    filename=item.data.get('filename',item.title)
    return Response(content=item.blob,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe='')})
from .account_connectors import router as account_connections_router
app.include_router(account_connections_router)
from .weather import router as weather_router
app.include_router(weather_router)
from .university import router as university_router
app.include_router(university_router)
from .university_calendar import router as university_calendar_router
app.include_router(university_calendar_router)
from .printing import router as printing_router
app.include_router(printing_router)
from .prisma import router as prisma_router
app.include_router(prisma_router)

@app.get('/api/v1/connectors')
def connectors(user:UserDep,db:DbDep):
    return [{'id':c.id,'name':c.name,'status':c.status,'scopes':c.scopes,'last_sync':c.last_sync,'error':c.error,'details':c.sync_details,'implemented':c.id in ['gmail','calendar','drive','rss','server','github','notion','vercel','weather','university','canva','university-calendar']} for c in db.scalars(select(Connector).where(Connector.user_id==user.id,Connector.id.notin_(['google-application','_whatsapp-control','_canva-application','_printing-discovery','github-releases'])).order_by(Connector.name))]
@app.post('/api/v1/connectors/{key}/authorize')
def authorize(key:str,user:UserDep,db:DbDep): return oauth_start(db,user,key)
@app.get('/api/v1/oauth/google/callback')
def callback(db:DbDep,state:str,code:str='',error:str|None=None):
    ok=oauth_callback(db,state,code,error); return RedirectResponse('/?section=connections&oauth='+('ok' if ok else 'cancelled'))
@app.post('/api/v1/connectors/{key}/sync')
def synchronize(key:str,user:UserDep,db:DbDep):
    if key in ['_whatsapp-control','_canva-application','_printing-discovery']:raise HTTPException(404,'Conector no encontrado')
    if key=='university-calendar':
        from .university_calendar import sync_uv_calendar
        return sync_uv_calendar(db,user)
    if key=='canva':
        from .prisma import sync_canva
        return sync_canva(db,user)
    return sync_connector(db,user,key)
@app.patch('/api/v1/connectors/{key}')
def pause_connector(key:str,payload:dict,user:UserDep,db:DbDep):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    if key in ['_whatsapp-control','_canva-application','_printing-discovery']:raise HTTPException(404,'Conector no encontrado')
    if payload.get('status') not in ['paused','connected']: raise HTTPException(422,'Estado inválido')
    c=db.get(Connector,(key,user.id))
    if not c: raise HTTPException(404,'Conector no encontrado')
    if payload['status']=='connected' and key in ['gmail','calendar','drive','github','notion','vercel','university','canva'] and not c.encrypted_tokens: raise HTTPException(409,'Conecta la cuenta primero')
    c.status=payload['status'];c.sync_started_at=None
    db.execute(update(Connector).where(Connector.id==key,Connector.user_id==user.id).values(revision=Connector.revision+1))
    audit(db,user,'connector.'+c.status,key); db.commit(); return {'ok':True}
@app.delete('/api/v1/connectors/{key}')
def disconnect(key:str,user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    if key in ['_whatsapp-control','_canva-application','_printing-discovery']:raise HTTPException(404,'Conector no encontrado')
    c=db.get(Connector,(key,user.id))
    if not c: raise HTTPException(404,'Conector no encontrado')
    consume_confirmation(db,user,'connector.disconnect',{'id':key},x_confirmation)
    if c.encrypted_tokens and key=='canva':
        from .prisma import revoke_canva
        revoke_canva(db,user)
    if c.encrypted_tokens and key in ['gmail','calendar','drive']:
        import httpx
        token=decode_tokens(c.encrypted_tokens)
        try:
            r=httpx.post('https://oauth2.googleapis.com/revoke',data={'token':token.get('refresh_token') or token.get('access_token')},timeout=15)
            if r.status_code not in [200,400]: raise HTTPException(502,'Google no ha confirmado la revocación. Inténtalo de nuevo.')
        except httpx.HTTPError: raise HTTPException(502,'No se pudo revocar el permiso en Google. Inténtalo de nuevo.')
    c.encrypted_tokens=None;c.sync_started_at=None
    db.execute(update(Connector).where(Connector.id==key,Connector.user_id==user.id).values(revision=Connector.revision+1))
    if key=='weather': c.sync_details={};c.last_sync=None
    c.status='disconnected'; c.error=None; audit(db,user,'connector.disconnected',key); db.commit(); return {'ok':True}
@app.get('/api/v1/server')
def server(user:UserDep): return server_health()
@app.post('/api/v1/server/actions')
def action(payload:dict,user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    if user.email!=settings.admin_email:
        raise HTTPException(403,'Solo el administrador puede ejecutar acciones sobre el servidor compartido')
    name=payload.get('action')
    if name not in ['service.restart','backup.run']: raise HTTPException(422,'Acción no permitida')
    consume_confirmation(db,user,name,payload,x_confirmation)
    result=server_action(name,payload.get('service')); audit(db,user,name,payload.get('service','')); db.commit(); return result
@app.get('/api/v1/chat')
def chat_history(user:UserDep,db:DbDep,project_id:str|None=None,thread_id:str='chat',all_projects:bool=False):
    from .database import AgentJob
    from .conversation_engine import message_id,project_for_context
    from .openclaw import history_is_authorized
    if project_id:
        project_for_context(db,user,project_id)
        if thread_id=='chat':thread_id=project_id
        if thread_id!=project_id:raise HTTPException(422,'La conversación no coincide con el proyecto.')
    job_query=select(AgentJob).where(AgentJob.user_id==user.id,
        AgentJob.execution['command']['channel'].as_string()=='chat')
    if not all_projects:
        job_query=job_query.where(AgentJob.thread_id==thread_id,
            AgentJob.execution['command']['project_id'].as_string()==project_id if project_id else AgentJob.execution['command']['project_id'].as_string().is_(None))
    jobs=list(db.scalars(job_query.order_by(AgentJob.created_at.desc(),AgentJob.id.desc()).limit(100)))
    by_message={message_id(job.id,role):job for job in jobs if (job.execution or {}).get('capability')=='conversation.integrated' for role in ['user','assistant']}
    message_query=select(Chat).where(Chat.user_id==user.id)
    if not all_projects:
        if project_id or thread_id!='chat':message_query=message_query.where(Chat.id.in_(by_message))
        else:
            origins=db.scalars(select(AgentJob.id).where(AgentJob.user_id==user.id,AgentJob.execution['capability'].as_string()=='conversation.integrated'))
            integrated_ids=[message_id(ident,role) for ident in origins for role in ['user','assistant']]
            message_query=message_query.where(or_(Chat.id.in_(by_message),Chat.id.notin_(integrated_ids)))
    messages=list(db.scalars(message_query.order_by(Chat.created_at.desc(),Chat.id.desc()).limit(100)))
    ids={s.get('id') for m in messages for s in (m.sources or []) if isinstance(s,dict) and isinstance(s.get('id'),str)}
    allowed=set(db.scalars(select(Item.id).where(Item.user_id==user.id,Item.id.in_(ids),Item.status.notin_(['archived','cancelled','canceled','withdrawn']),visible_items_clause(db,user)))) if ids else set()
    output=[];job_access={}
    for m in reversed(messages):
        job=by_message.get(m.id)
        if job and job.id not in job_access:job_access[job.id]=history_is_authorized(db,user,job)
        revoked=any(isinstance(s,dict) and s.get('id') and s['id'] not in allowed for s in (m.sources or [])) or bool(m.role=='assistant' and job and not job_access[job.id])
        output.append({'id':m.id,'role':m.role,'text':'El resultado histórico contiene fuentes que ya no están disponibles.' if revoked else m.text,'sources':[] if revoked else m.sources,'mode':m.mode,'created_at':m.created_at,'resources_unavailable':revoked,'job_id':job.id if job else None,'context_sources':job.context_sources if job and job_access[job.id] else [],'project_id':(job.execution or {}).get('command',{}).get('project_id') if job else None,'thread_id':job.thread_id if job else 'chat'})
    return output
@app.post('/api/v1/chat')
def chat_message(payload:ChatIn,user:UserDep,db:DbDep):
    if payload.integrated:
        from .conversation_engine import enqueue as conversation_enqueue
        job=conversation_enqueue(db,user,payload.text,idempotency_key=payload.idempotency_key,authorize_internal_write=payload.authorize_internal_write,area=payload.area,item_ids=payload.item_ids,project_id=payload.project_id,thread_id=payload.thread_id)
        return JSONResponse(job,status_code=202)
    if payload.project_id or payload.thread_id!='chat' or payload.item_ids:raise HTTPException(422,'El contexto seleccionado requiere la conversación integrada.')
    return answer(db,user,payload.text)
@app.delete('/api/v1/chat')
def clear_chat(user:UserDep,db:DbDep,x_confirmation:str|None=Header(default=None)):
    consume_confirmation(db,user,'chat.clear',{},x_confirmation)
    from .database import AgentJob
    import copy
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    for job in db.scalars(select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.execution['command']['channel'].as_string()=='chat').with_for_update()):
        if (job.execution or {}).get('capability')!='conversation.integrated':continue
        execution=copy.deepcopy(job.execution);execution['chat_cleared_at']=now()
        if job.status in ['pending','running']:
            execution['state']='cancelled';execution['state_version']+=1;job.status='cancelled';job.finished_at=now()
        job.execution=execution
    db.execute(delete(Chat).where(Chat.user_id==user.id));audit(db,user,'chat.cleared');db.commit();return {'ok':True}
@app.get('/api/v1/automations')
def automations(user:UserDep,db:DbDep):
    return [{'id':a.id,'title':a.title,'kind':a.kind,'enabled':a.enabled,'interval_minutes':a.interval_minutes,'next_run':a.next_run} for a in db.scalars(select(Automation).where(Automation.user_id==user.id).order_by(Automation.title))]
@app.post('/api/v1/automations',status_code=201)
def create_automation(payload:AutomationIn,user:UserDep,db:DbDep):
    a=Automation(user_id=user.id,**payload.model_dump()); db.add(a); audit(db,user,'automation.created',a.title); db.commit(); return {'id':a.id}
@app.patch('/api/v1/automations/{automation_id}')
def update_automation(automation_id:str,payload:AutomationIn,user:UserDep,db:DbDep):
    a=db.scalar(select(Automation).where(Automation.id==automation_id,Automation.user_id==user.id))
    if not a: raise HTTPException(404,'Automatización no encontrada')
    for k,v in payload.model_dump().items(): setattr(a,k,v)
    audit(db,user,'automation.updated',a.id); db.commit(); return {'ok':True}
@app.post('/api/v1/automations/{automation_id}/run',status_code=202)
def run_automation(automation_id:str,user:UserDep,db:DbDep):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    a=db.scalar(select(Automation).where(Automation.id==automation_id,Automation.user_id==user.id))
    if not a: raise HTTPException(404,'Automatización no encontrada')
    active=db.scalar(select(Run).where(Run.automation_id==a.id,Run.status.in_(['pending','running'])))
    if active: return {'id':active.id,'status':active.status}
    r=Run(user_id=user.id,automation_id=a.id,origin='manual'); db.add(r); audit(db,user,'automation.queued',a.id); db.commit(); return {'id':r.id,'status':'pending'}
@app.get('/api/v1/automations/runs/history')
def runs(user:UserDep,db:DbDep):
    return [{'id':r.id,'automation_id':r.automation_id,'status':r.status,'result':r.result,'created_at':r.created_at,'finished_at':r.finished_at} for r in db.scalars(select(Run).where(Run.user_id==user.id).order_by(Run.created_at.desc()).limit(100))]
@app.get('/api/v1/activity')
def activity(user:UserDep,db:DbDep): return [{'id':a.id,'action':a.action,'subject':a.subject,'created_at':a.created_at} for a in db.scalars(select(Audit).where(Audit.user_id==user.id).order_by(Audit.created_at.desc()).limit(100))]
@app.get('/api/v1/export')
def export(user:UserDep,db:DbDep):
    payload={'user':{'email':user.email,'ai_consent':user.ai_consent},'items':[item_dict(i) for i in db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id))],'inbox':[inbox_dict(i) for i in db.scalars(select(Inbox).where(Inbox.user_id==user.id))],'chat':chat_history(user,db),'agent_runs':agent_runs(user,db),'automations':automations(user,db),'activity':activity(user,db)}
    audit(db,user,'privacy.exported'); db.commit()
    return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="aros-export.json"'})

app.include_router(agent_router)
web=Path(settings.web_dir)
if web.is_dir(): app.mount('/',StaticFiles(directory=web,html=True),name='web')
