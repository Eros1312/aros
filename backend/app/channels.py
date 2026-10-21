"""Only specific channel operations are exposed; never arbitrary admin RPC."""
from typing import Annotated
from datetime import datetime,timezone,timedelta
import httpx
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from .database import User,Connector,get_db,now
from .security import current_user,audit
from .config import settings
router=APIRouter(prefix='/api/v1/openclaw',tags=['OpenClaw channels'])
UserDep=Annotated[User,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]
def require_owner(user):
    if user.email!=settings.admin_email: raise HTTPException(403,'Solo el propietario administra los canales de OpenClaw')

def rpc(method,params):
    if not settings.openclaw_url or not settings.openclaw_token: raise HTTPException(503,'OpenClaw no está configurado')
    try:
        with httpx.Client(timeout=30) as client:
            r=client.post(settings.openclaw_url.rstrip('/')+'/api/v1/admin/rpc',headers={'Authorization':'Bearer '+settings.openclaw_token},json={'method':method,'params':params})
            r.raise_for_status();body=r.json()
        if not body.get('ok'): raise ValueError('rpc')
        return body.get('payload',{})
    except (httpx.HTTPError,ValueError,TypeError): raise HTTPException(502,'OpenClaw no pudo completar la operación del canal. Actualiza o vuelve a intentar la conexión.') from None

@router.get('/channels')
def channels(user:UserDep):
    require_owner(user)
    data=rpc('channels.status',{})
    accounts=data.get('channelAccounts',{}).get('whatsapp',[])
    # Do not return phone numbers, credentials, gateway configuration or unrelated accounts.
    connected=any(a.get('connected') for a in accounts)
    linked=any(a.get('linked') for a in accounts)
    return {'whatsapp':{'connected':connected,'linked':linked,'detail':'Conectado a OpenClaw' if connected else 'Vinculado; esperando conexión' if linked else 'Pendiente de vincular con QR'},'policy':'Solo el chat contigo mismo. Grupos desactivados.','other_channels':[{'id':'telegram','name':'Telegram','detail':'Requiere un token de bot propio'},{'id':'google','name':'Google · Gmail y Calendar','detail':'Configura OAuth en Conexiones de Aros'}]}

def patch_config(patch):
    import json
    snapshot=rpc('config.get',{})
    if not snapshot.get('hash'): raise HTTPException(502,'No se pudo verificar la configuración actual de OpenClaw')
    rpc('config.patch',{'raw':json.dumps(patch),'baseHash':snapshot['hash'],'note':'Configuración de Aros'})

CONTROL='_whatsapp-control'
def control(db,user):
    row=db.get(Connector,(CONTROL,user.id))
    if not row:
        row=Connector(id=CONTROL,user_id=user.id,name='Control privado de WhatsApp',scopes=[],sync_details={'desired':'disabled','pending':True})
        db.add(row);db.flush()
    return row

def whatsapp_policy(enabled,phone=None):
    # Apply at both levels: existing account overrides must not retain open DMs.
    policy={'enabled':enabled,'dmPolicy':'allowlist','allowFrom':[phone] if phone else [],'selfChatMode':True,'groupPolicy':'disabled'}
    return {'channels':{'whatsapp':{**policy,'accounts':{'default':dict(policy)}}}}

def _shutdown(db,user,row):
    try:
        patch_config(whatsapp_policy(False))
        rpc('channels.logout',{'channel':'whatsapp','accountId':'default'})
    except HTTPException:
        row.sync_details={'desired':'disabled','pending':True,'retry_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()}
        row.status='error';row.error='Desconexión pendiente de confirmar en OpenClaw';db.commit()
        return False
    row.sync_details={'desired':'disabled','pending':False};row.status='disconnected';row.error=None;db.commit()
    return True

def request_whatsapp_shutdown(db,user):
    require_owner(user)
    row=control(db,user)
    row.sync_details={'desired':'disabled','pending':True};row.encrypted_tokens=None;row.status='disconnecting';db.commit()
    return _shutdown(db,user,row)

def reconcile_whatsapp(db):
    user=db.scalar(select(User).where(User.email==settings.admin_email))
    if not user:return
    row=db.get(Connector,(CONTROL,user.id))
    if not row:
        if user.ai_consent:return
        row=control(db,user);db.commit()
    state=row.sync_details or {}
    if user.ai_consent and not state.get('pending'):return
    if not user.ai_consent and state.get('desired')!='disabled':
        row.sync_details={'desired':'disabled','pending':True};row.encrypted_tokens=None;db.commit();state=row.sync_details
    if not state.get('pending'):return
    retry_at=state.get('retry_at')
    if retry_at and datetime.fromisoformat(retry_at)>datetime.now(timezone.utc):return
    _shutdown(db,user,row)

class StartIn(BaseModel):
    phone:str=Field(pattern=r'^\+[1-9][0-9]{7,14}$',max_length=16)
@router.post('/whatsapp/start')
def start(payload:StartIn,user:UserDep,db:DbDep):
    require_owner(user)
    if not user.ai_consent: raise HTTPException(409,'Activa IA externa antes de vincular WhatsApp. Tus mensajes al agente se procesarán con el modelo conectado.')
    from .connectors import encode_tokens
    encrypted_phone=encode_tokens({'phone':payload.phone})
    patch_config(whatsapp_policy(True,payload.phone))
    row=control(db,user);row.encrypted_tokens=encrypted_phone;row.sync_details={'desired':'enabled','pending':False};row.status='configured';row.error=None;db.commit()
    data=rpc('web.login.start',{'channel':'whatsapp','accountId':'default','timeoutMs':15000})
    audit(db,user,'channel.login_started','whatsapp');db.commit()
    return safe_login(data)
class WaitIn(BaseModel):
    session_key:str|None=Field(default=None,max_length=500)
    qr_data_url:str|None=Field(default=None,max_length=16384,pattern=r'^data:image/png;base64,')
@router.post('/whatsapp/wait')
def wait(payload:WaitIn,user:UserDep):
    require_owner(user)
    if not user.ai_consent:raise HTTPException(409,'Activa IA externa antes de continuar la conexión de WhatsApp')
    params={'channel':'whatsapp','accountId':'default','timeoutMs':1000}
    if payload.session_key: params['sessionKey']=payload.session_key
    if payload.qr_data_url: params['currentQrDataUrl']=payload.qr_data_url
    return safe_login(rpc('web.login.wait',params))
def safe_login(data):
    qr=data.get('qrDataUrl')
    if not isinstance(qr,str) or not qr.startswith('data:image/png;base64,') or len(qr)>16384: qr=None
    return {'connected':bool(data.get('connected')),'qr_data_url':qr,'session_key':data.get('sessionKey'),'message':'WhatsApp conectado' if data.get('connected') else 'En WhatsApp abre Ajustes → Dispositivos vinculados → Vincular un dispositivo y escanea el QR.'}

@router.post('/whatsapp/disconnect')
def disconnect(user:UserDep,db:DbDep):
    require_owner(user)
    disconnected=request_whatsapp_shutdown(db,user)
    audit(db,user,'channel.disconnected','whatsapp');db.commit()
    return {'disconnected':disconnected,'pending':not disconnected}
