from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from sqlalchemy.orm import defer
from fastapi import HTTPException
from .database import Item, Inbox, Audit, Connector, now
from .security import audit
from .schemas import ItemOut
from .config import settings

def owned_item(db,user,item_id):
    from .source_access import visible_items_clause
    item=db.scalar(select(Item).where(Item.id==item_id,Item.user_id==user.id,visible_items_clause(db,user)))
    if not item: raise HTTPException(404,'No se ha encontrado el recurso')
    return item

def item_dict(item): return ItemOut.model_validate(item).model_dump()
def inbox_dict(item):
    return {k:getattr(item,k) for k in ['id','item_id','title','reason','area','state','snoozed_until','created_at']}
def wake_inbox(db,user):
    db.execute(update(Inbox).where(Inbox.user_id==user.id,Inbox.state=='snoozed',Inbox.snoozed_until <= now()).values(state='new',snoozed_until=None))
    db.commit()
def attention_for_item(db,user,item,reopen=False):
    alerts=list(db.scalars(select(Inbox).where(Inbox.user_id==user.id,Inbox.item_id==item.id)))
    if item.status in ['done','archived','cancelled']:
        for alert in alerts: alert.state='resolved'
    elif item.kind in ['task','reminder'] and (item.priority in ['high','urgent'] or item.due_at):
        reason='Prioridad alta.' if not item.due_at else 'Tiene una fecha de entrega o recordatorio.'
        if alerts:
            for alert in alerts:
                alert.title=item.title; alert.reason=reason
                if reopen and alert.state in ['resolved','archived']: alert.state='new'
        else: db.add(Inbox(user_id=user.id,item_id=item.id,title=item.title,reason=reason,area=item.area))

def counted(number,singular,plural): return f"{number} {singular if number==1 else plural}"

def dashboard(db,user):
    wake_inbox(db,user)
    from .source_access import visible_items_clause
    items=list(db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.status.notin_(['archived','cancelled']),visible_items_clause(db,user)).order_by(Item.due_at,Item.created_at)))
    attention=list(db.scalars(select(Inbox).where(Inbox.user_id==user.id,Inbox.state.in_(['new','seen'])).order_by(Inbox.created_at)))
    actionable={i.id for i in items if i.kind in ['task','reminder'] and i.status!='done'}
    attention=[notice for notice in attention if notice.item_id in actionable]
    from .seed import AREAS
    rows=[]
    for area in AREAS:
        group=[i for i in items if i.area==area]
        tasks=[i for i in group if i.kind in ['task','reminder'] and i.status!='done']
        events=[i for i in group if i.kind=='event' and i.due_at and i.due_at >= now()]
        event=events[0] if events else None
        documents=[i for i in group if i.kind=='document']
        summaries=[]
        if tasks: summaries.append({'id':area+'-tasks','title':counted(len(tasks),'tarea pendiente','tareas pendientes'),'subtitle':tasks[0].title,'symbol':'list.bullet.clipboard'})
        if documents: summaries.append({'id':area+'-docs','title':counted(len(documents),'documento','documentos'),'subtitle':documents[0].title,'symbol':'doc.text'})
        others=[i for i in group if i.kind not in ['task','reminder','event','document']]
        summaries.extend({'id':i.id,'title':i.title,'subtitle':i.description[:90] or i.status,'symbol':'sparkles' if i.kind=='news' else 'tray'} for i in others[:3-len(summaries)])
        if area in ['technology','resources','personal']:
            summaries=[]
            def row(key,title,subtitle,symbol):
                summaries.append({'id':area+'-'+key,'title':title,'subtitle':subtitle,'symbol':symbol})
            if area=='technology':
                news=[i for i in group if i.kind=='news']
                projects=[i for i in group if i.kind=='project']
                if news: row('news',counted(len(news),'novedad','novedades'),'Ejemplos' if all(i.is_demo for i in news) else 'Tu selección de noticias','sparkles')
                if projects: row('projects',counted(len(projects),'proyecto','proyectos'),'Ideas y herramientas','folder')
                if tasks: row('tasks',f'{len(tasks)} pendientes','Tu siguiente paso','checklist')
            elif area=='resources':
                notes=[i for i in group if i.kind=='note']; links=[i for i in group if i.kind=='link']; projects=[i for i in group if i.kind=='project']
                if documents: row('documents',counted(len(documents),'documento','documentos'),'Tu biblioteca de documentos','doc.text')
                if notes: row('notes',counted(len(notes),'nota','notas'),'Ideas y referencias','note.text')
                if links: row('links',counted(len(links),'enlace','enlaces'),'Referencias guardadas','link')
                if projects: row('projects',counted(len(projects),'proyecto','proyectos'),'Organiza tus recursos','folder')
            else:
                orders=[i for i in group if i.kind=='order' and i.status not in ['done','delivered']]; expenses=[i for i in group if i.kind=='expense' and i.status!='done']
                if orders: row('orders',counted(len(orders),'pedido en curso','pedidos en curso'),' · '.join(dict.fromkeys(i.data.get('store') or 'Sin tienda' for i in orders)),'shippingbox')
                if expenses: row('expenses',counted(len(expenses),'pago pendiente','pagos pendientes'),'Revisa tus gastos','creditcard')
                if tasks: row('tasks',counted(len(tasks),'tarea pendiente','tareas pendientes'),'Tu lista personal','checklist')
                if not summaries:
                    notes=[i for i in group if i.kind in ['note','memory']]
                    if notes: row('notes',counted(len(notes),'nota','notas'),'Ideas y memoria','note.text')
        if not summaries: summaries=[{'id':area+'-empty','title':'Sin datos todavía','subtitle':'Añade contenido o conecta una fuente.','symbol':'checkmark.circle'}]
        rows.append({'area':area,'eyebrow':'Próximo evento' if event else None,'headline':event.title if event else None,'detail':event.due_at if event else None,'footnote':event.data.get('location') if event else None,'rows':summaries})
    weather_row=db.get(Connector,('weather',user.id))
    details=weather_row.sync_details if weather_row else {}
    forecast=details.get('forecast') or {}
    weather_label=forecast.get('description','Sin conectar')
    if weather_row and (weather_row.status in ['error','paused'] or not weather_row.last_sync or datetime.fromisoformat(weather_row.last_sync)<datetime.now(timezone.utc)-timedelta(minutes=30)):weather_label+=' · Sin actualizar'
    city=details.get('location',{}).get('label','Sin ubicación').split(',')[0]
    temperature=str(round(forecast['temperature']))+' °C' if 'temperature' in forecast else '—'
    successes=[]
    for value in db.scalars(select(Connector.last_sync).where(Connector.user_id==user.id,Connector.status!='disconnected',Connector.last_sync.is_not(None))):
        try:
            instant=datetime.fromisoformat(value.replace('Z','+00:00'))
            if instant.tzinfo:successes.append(instant.astimezone(timezone.utc))
        except (ValueError,TypeError):pass
    synced=max(successes).isoformat() if successes else None
    return {'city':'Ciudad de ejemplo' if settings.demo_mode else city,'temperature':'24 °C' if settings.demo_mode else temperature,'weather':'Ejemplo' if settings.demo_mode else weather_label,'summaries':rows,'attention':[{'id':i.id,'area':i.area,'title':i.title,'reason':i.reason,'resolved':False} for i in attention],'metrics':[],'syncedAt':synced,'generatedAt':now(),'demo':settings.demo_mode,'counts':{'tasks':sum(i.kind in ['task','reminder'] and i.status!='done' for i in items),'documents':sum(i.kind=='document' for i in items)},'events':[item_dict(i) for i in items if i.kind=='event' and i.due_at and i.due_at>=now()][:8]}

def validate_transition(db,user,item,payload):
    internal_conversation=item.source=='agent-conversation' and item.kind in ['note','task','reminder','event','project']
    if item.source not in ['manual','demo','upload'] and not internal_conversation:
        raise HTTPException(409,'Este recurso se sincroniza desde su fuente. Modifica el original o crea una copia manual.')
    values=payload.model_dump(exclude={'version'})
    if internal_conversation:
        if payload.kind!=item.kind or payload.area!=item.area:
            raise HTTPException(422,'El tipo y el área de este resultado son inmutables. Crea una copia para reorganizarlo.')
        # Normal UI editing changes content, never the authorization evidence.
        # An omitted data field preserves metadata; a full unchanged echo is
        # accepted, but removal/forgery (including unknown future keys) is not.
        if 'data' in payload.model_fields_set and payload.data!=(item.data or {}):
            raise HTTPException(422,'Los metadatos y la procedencia de este resultado son inmutables; edita título, descripción, estado, prioridad o fecha.')
        if item.kind=='event' and (item.data or {}).get('all_day') and payload.due_at!=item.due_at:
            raise HTTPException(422,'Cambiar la fecha de un evento de día completo exige ajustar también su intervalo. Crea una copia con el nuevo intervalo; el original se conserva.')
        values['data']=item.data
    changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==payload.version).values(**values,version=Item.version+1,updated_at=now()))
    if changed.rowcount != 1: raise HTTPException(409,'El recurso ha cambiado. Recarga antes de editarlo.')
    db.expire(item); db.refresh(item)
    attention_for_item(db,user,item,reopen=True)
    audit(db,user,'item.updated',item.id)
    db.commit(); return item
