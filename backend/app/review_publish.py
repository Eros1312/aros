"""One internal review draft per area and Madrid day, with current provenance."""
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select
from .database import Item, Audit, now
from .conversation_engine import revalidate, AREAS
from .conversation_tools import receipt
from .security import audit


def review_scope(agent_id):
    return {'areas':list(AREAS) if agent_id=='aros' else [agent_id],
            'area':'personal' if agent_id=='aros' else agent_id,'item_ids':[]}


def publish_daily_review(db,user,job,text,references):
    """Caller commits the draft and terminal job lease in the same transaction."""
    if not job.is_review or not references or not isinstance(text,str) or len(text.strip())<30:return None
    if not user.ai_consent:return None
    scope=review_scope(job.agent_id)
    rows=revalidate(db,user,scope,references)
    if any(row.source=='agent-review' for row in rows.values()):return None
    day=datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat()
    ident=str(uuid5(NAMESPACE_URL,f'aros-review:{user.id}:{job.agent_id}:{day}'))
    if db.scalar(select(Audit.id).where(Audit.user_id==user.id,Audit.action=='item.deleted',Audit.subject==ident).limit(1)):return None
    item=db.scalar(select(Item).where(Item.id==ident,Item.user_id==user.id).with_for_update().execution_options(populate_existing=True))
    if item and (item.source!='agent-review' or item.status in ['archived','cancelled','canceled','withdrawn']):return None
    if item and (item.data or {}).get('execution_id')==job.id:return receipt(item)
    names={'aros':'Aros','university':'Universidad','prisma':'PRISMA','technology':'IA y tecnología','printing':'Impresión 3D','personal':'Personal','server':'Servidor'}
    data={'execution_id':job.id,'review_date':day,'review_agent':job.agent_id,'review_state':'draft','automatic_review':True,
          'source_references':[{'id':ref['id'],'version':ref['version'],'content_hash':ref['content_hash']} for ref in references]}
    description='Borrador automático de la revisión diaria.\n\n'+text.strip()[:12000]
    if len(text.strip())>12000:description+='\n\nResumen abreviado; la respuesta completa se conserva en el trabajo.'
    if item:
        item.description=description;item.data=data;item.version+=1;item.updated_at=now()
    else:
        item=Item(id=ident,user_id=user.id,kind='note',area=scope['area'],source='agent-review',external_id=job.agent_id+':'+day,
                  title=f'Revisión diaria · {names.get(job.agent_id,job.agent_id)} · {day}',description=description,status='active',data=data)
        db.add(item)
    db.flush();audit(db,user,'agent.review_draft_saved',item.id)
    return receipt(item)
