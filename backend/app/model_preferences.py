"""Model selection never implies a fallback or a new credential permission."""
import time
import httpx
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from .config import settings
from .database import User,get_db
from .security import current_user,audit
DEFAULT_MODEL=settings.default_model
router=APIRouter(prefix='/api/v1/ai',tags=['AI preferences'])
_cache={'until':0,'key':None,'models':[],'detail':''}

class Selection(BaseModel):
    model:str=Field(min_length=1,max_length=240)

def available_models():
    key=(settings.freellm_url,settings.freellm_token,settings.freellm_models)
    if _cache['key']==key and time.monotonic()<_cache['until']: return _cache['models'],_cache['detail']
    models=[];detail='FreeLLMAPI aún no está configurado.'
    if settings.freellm_url and settings.freellm_token:
        try:
            with httpx.Client(timeout=4) as client:
                r=client.get(settings.freellm_url.rstrip('/')+'/v1/models?available=true',headers={'Authorization':'Bearer '+settings.freellm_token});r.raise_for_status();payload=r.json()
            registered=set(settings.freellm_models.split(','))
            for entry in payload.get('data',[]):
                id=entry.get('id','')
                if id in registered and not id.startswith(('auto','fusion','claude-')):
                    models.append({'id':'freellmapi/'+id,'name':entry.get('name') or id,'provider':'FreeLLMAPI','detail':'Proveedor externo · modelo elegido explícitamente'})
            detail='Modelos con proveedor habilitado en tu instancia.' if models else 'FreeLLMAPI está instalado. Añade un proveedor en su panel privado para disponer de modelos.'
        except (httpx.HTTPError,ValueError,TypeError): detail='No se puede consultar el catálogo privado de FreeLLMAPI.'
    _cache.update(key=key,until=time.monotonic()+30,models=models,detail=detail)
    return models,detail

def validate_model(model):
    if model==DEFAULT_MODEL: return
    if model not in {entry['id'] for entry in available_models()[0]}:
        raise HTTPException(409,'Ese modelo no está registrado y disponible. Actualiza el catálogo o conecta su proveedor.')

@router.get('/preferences')
def preferences(user:Annotated[User,Depends(current_user)]):
    models,detail=available_models()
    return {'model':user.ai_model,'default_model':DEFAULT_MODEL,'models':[{'id':DEFAULT_MODEL,'name':DEFAULT_MODEL if DEFAULT_MODEL!='local' else 'Resumen local (sin IA)','provider':'OpenClaw' if settings.openclaw_url else 'OpenAI' if settings.openai_api_key else 'Local','detail':'Modelo elegido por el administrador; requiere un proveedor autorizado.' if DEFAULT_MODEL!='local' else 'Reglas locales; no se envían datos a un modelo.'}]+models,'freellm_detail':detail,'scope':'Chat y nuevas consultas de todos los agentes. Las consultas ya encoladas conservan su modelo.','automatic_fallback':False}

@router.patch('/preferences')
def choose(payload:Selection,user:Annotated[User,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    validate_model(payload.model)
    if payload.model.startswith('freellmapi/') and not user.ai_consent:
        raise HTTPException(409,'Activa IA externa antes de elegir un proveedor de FreeLLMAPI. Recibirá tu pregunta y el contexto necesario.')
    if settings.app_env=='production' and settings.openclaw_url and user.email==settings.admin_email:
        from .channels import patch_config
        from .openclaw import AGENTS
        patch_config({'agents':{'defaults':{'model':{'primary':payload.model,'fallbacks':[]}},'entries':{id:{'model':{'primary':payload.model,'fallbacks':[]}} for id in AGENTS}}})
    user.ai_model=payload.model;audit(db,user,'ai.model_selected',payload.model);db.commit()
    return preferences(user)
