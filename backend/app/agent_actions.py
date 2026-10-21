"""A bounded resource action layer. Models cannot choose hosts, users or SQL."""
from fastapi import HTTPException
from pydantic import ValidationError,BaseModel,Field,ConfigDict
import re
from .schemas import ItemIn
from .database import Item,now
from .services import owned_item,attention_for_item
from sqlalchemy import update

class EventDesignIn(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    brief:str=Field(min_length=10,max_length=4000)
    asset_id:str|None=Field(default=None,min_length=1,max_length=36)

def design_requested(text):
    """Only current human design requests admit this action; resource text cannot."""
    low=text.lower()
    if re.search(r'\b(no|sin)\s+(?:me\s+)?(?:cre(?:ar|es)|diseñ(?:ar|es)|hag(?:as|a)|prepar(?:ar|es))\b',low):return False
    subject=re.search(r'\b(canva|diseño|diseños|cartel|carteles|póster|poster|rasca|rascas|pegatina|pegatinas|logo)\b',low)
    intent=re.search(r'\b(diseña|diseñar|crea|crear|haz|hacer|prepara|preparar|genera|generar)\b',low)
    if not intent:intent=re.search(r'\b(quiero|necesito|solicito)\s+(?:un(?:a)?\s+)?(?:diseño|cartel|póster|poster|rasca|pegatina|logo)\b',low)
    return bool(subject and intent)

def collect_actions(raw,agent,allow_design=True):
    result=[]
    if not isinstance(raw,list): return result
    for action in raw[:5]:
        if not isinstance(action,dict): continue
        try:
            if action.get('type')=='item.create':
                payload=ItemIn.model_validate(action.get('payload')).model_dump()
                if agent!='aros' and payload['area']!=agent: continue
            elif action.get('type')=='item.complete':
                p=action.get('payload',{})
                if not isinstance(p.get('id'),str) or not isinstance(p.get('version'),int) or p['version']<1: continue
                payload={'id':p['id'],'version':p['version']}
            elif action.get('type')=='events.design':
                if agent not in ['aros','events'] or not allow_design:continue
                payload=EventDesignIn.model_validate(action.get('payload')).model_dump()
            else: continue
            result.append({'type':action['type'],'payload':payload})
        except (ValidationError,TypeError,AttributeError): continue
    return result

def apply_action(db,user,action,agent):
    if action['type']=='events.design':
        if agent not in ['aros','events']:raise HTTPException(403,'Solo Aros y Eventos pueden preparar diseños Canva')
        from .events import queue_design,BriefIn
        payload=EventDesignIn.model_validate(action['payload'])
        # Queue and the applied receipt share the caller's transaction.
        return queue_design(BriefIn(**payload.model_dump()),user,db)
    if action['type']=='item.create':
        payload=ItemIn.model_validate(action['payload'])
        item=Item(user_id=user.id,**payload.model_dump());db.add(item);db.flush()
    elif action['type']=='item.complete':
        p=action['payload'];item=owned_item(db,user,p['id'])
        if agent!='aros' and item.area!=agent: raise HTTPException(403,'El recurso pertenece a otra área')
        if item.source not in ['manual','demo','upload']: raise HTTPException(409,'Modifica este recurso en su fuente original')
        if item.kind not in ['task','reminder','project','note']: raise HTTPException(409,'Esta acción solo completa tareas, recordatorios, proyectos y notas')
        changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==p['version']).values(status='done',version=Item.version+1,updated_at=now()))
        if changed.rowcount!=1: raise HTTPException(409,'El recurso ha cambiado. Pide al agente que actualice la propuesta')
        db.expire(item);db.refresh(item)
    else: raise HTTPException(422,'Acción no disponible')
    attention_for_item(db,user,item,reopen=True)
    return item
