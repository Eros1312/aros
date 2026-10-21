import secrets, hashlib, hmac, json
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, delete
from .database import get_db, Session, User, Confirmation, Audit
from .config import settings

def hash_password(password):
    salt=secrets.token_bytes(16)
    key=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)
    return salt.hex()+':'+key.hex()
def verify_password(password,encoded):
    try:
        salt,key=encoded.split(':')
        return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex(),key)
    except (ValueError,TypeError): return False
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def payload_hash(payload): return digest(json.dumps(payload,sort_keys=True,separators=(',',':')))
def expiry(minutes): return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()
def expired(value): return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
def audit(db,user,action,subject=''):
    db.add(Audit(user_id=user.id, action=action,subject=subject[:240]))
def issue_session(db,user,response):
    token=secrets.token_urlsafe(48)
    db.add(Session(token_hash=digest(token),user_id=user.id,expires_at=expiry(settings.session_days*1440)))
    response.set_cookie('aros_session',token,httponly=True,secure=settings.app_env=='production',samesite='lax',max_age=settings.session_days*86400,path='/')
    db.commit()
    return {'access_token':token,'token_type':'bearer','user':{'id':user.id,'email':user.email},'demo':settings.demo_mode}
def current_user(request:Request,db=Depends(get_db)):
    auth=request.headers.get('authorization','')
    token=auth[7:] if auth.startswith('Bearer ') else request.cookies.get('aros_session','')
    session=db.get(Session,digest(token)) if token else None
    if not session or expired(session.expires_at): raise HTTPException(401,'Inicia sesión para continuar')
    user=db.get(User,session.user_id)
    if not user: raise HTTPException(401,'La sesión ya no es válida')
    return user
def csrf(request:Request):
    if request.method in {'POST','PUT','PATCH','DELETE'} and not request.headers.get('authorization','').startswith('Bearer '):
        origin=request.headers.get('origin')
        if origin is not None and origin != settings.public_origin: raise HTTPException(403,'Origen no permitido')
        if request.cookies.get('aros_session') and origin is None and request.headers.get('x-aros-client') != 'web':
            raise HTTPException(403,'Falta protección de la petición')
def consume_confirmation(db,user,action,payload,token):
    row=db.get(Confirmation,token or '')
    if not row or row.user_id != user.id or row.action != action or row.payload_hash != payload_hash(payload) or expired(row.expires_at):
        raise HTTPException(409,'Revisa y confirma esta acción antes de ejecutarla')
    removed=db.execute(delete(Confirmation).where(Confirmation.id==row.id,Confirmation.user_id==user.id))
    if removed.rowcount != 1: raise HTTPException(409,'Esta confirmación ya se ha utilizado')
