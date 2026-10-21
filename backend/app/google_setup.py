"""Per-owner encrypted OAuth application configuration."""
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from .database import User,Connector,get_db
from .security import current_user,audit
from .config import settings
router=APIRouter(prefix='/api/v1/google/application',tags=['Google setup'])
UserDep=Annotated[User,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]
CALLBACK='/api/v1/oauth/google/callback'
def credentials(db,user_id):
    from .connectors import decode_tokens
    row=db.get(Connector,('google-application',user_id))
    if row and row.encrypted_tokens:
        data=decode_tokens(row.encrypted_tokens)
        return data['client_id'],data['client_secret']
    return settings.google_client_id,settings.google_client_secret
@router.get('')
def status(user:UserDep,db:DbDep):
    client,secret=credentials(db,user.id)
    return {'configured':bool(client and secret),'redirect_uri':settings.public_origin+CALLBACK,'origin':settings.public_origin,'detail':'Gmail y Calendar importan información a Aros. La sincronización actual es de lectura; crear un recurso en Aros no envía correo ni cambia Google Calendar.'}
class SetupIn(BaseModel):
    credentials_json:str=Field(min_length=10,max_length=20000)
@router.post('')
def configure(payload:SetupIn,user:UserDep,db:DbDep):
    import json
    from .connectors import encode_tokens
    if any(db.get(Connector,(k,user.id)).encrypted_tokens for k in ['gmail','calendar','drive']):
        raise HTTPException(409,'Desconecta primero las cuentas de Google antes de cambiar su aplicación OAuth')
    try:
        raw=json.loads(payload.credentials_json)['web']
        client=raw['client_id'];secret=raw['client_secret'];redirects=raw['redirect_uris']
        if not isinstance(client,str) or not client.endswith('.apps.googleusercontent.com') or len(client)>500: raise ValueError()
        if not isinstance(secret,str) or not 8<=len(secret)<=500: raise ValueError()
        if settings.public_origin+CALLBACK not in redirects: raise ValueError()
    except (ValueError,TypeError,KeyError):
        raise HTTPException(422,'Usa un JSON de cliente OAuth web de Google con la URL de retorno indicada en este panel') from None
    row=db.get(Connector,('google-application',user.id))
    if not row:
        row=Connector(id='google-application',user_id=user.id,name='Aplicación OAuth Google',scopes=[]);db.add(row)
    row.encrypted_tokens=encode_tokens({'client_id':client,'client_secret':secret});row.status='configured'
    audit(db,user,'google.application_configured');db.commit()
    return status(user,db)
