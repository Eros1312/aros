"""Idempotent compatibility transition to generic workspace areas.

Retains all user files. Retired remote sources remain inaccessible; existing
jobs are never resumed under a changed tool or authorization contract.
"""
import copy
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import Session
from .database import Item, Inbox, AgentJob, AgentSchedule, Automation, Run, Connector, Chat, now
from .source_access import resource_fingerprint

AREAS={'university':'learning','prisma':'events','printing':'resources'}
SOURCES={'prisma-file':'events-file','prisma-context':'events-context','university-browser':'upload'}
RETIRED_SOURCES={'university','uv-calendar','printing-catalog','printing-generated'}
RETIRED_AUTOMATIONS={'university_sync','uv_calendar_sync','printing_discovery'}


def metadata(value,key=''):
    if isinstance(value,dict):return {k:metadata(v,k) for k,v in value.items()}
    if isinstance(value,list):return [metadata(v,key) for v in value]
    if isinstance(value,str):
        if key in {'area','areas','agent_id'}:return AREAS.get(value,value)
        if key=='source':return SOURCES.get(value,value)
        if key in {'type','tool','action'} and value=='prisma.design':return 'events.design'
        if key=='category' and value in {'scratch','sticker'}:return 'banner'
    return value


def generalize_workspace(connection):
    with Session(bind=connection) as db:
        # A fast no-op once every persisted identifier uses the current contract.
        legacy=(db.scalar(select(Item.id).where(or_(Item.area.in_(AREAS),Item.kind.in_(['model','material']))).limit(1))
                or db.scalar(select(Inbox.id).where(Inbox.area.in_(AREAS)).limit(1))
                or db.scalar(select(AgentJob.id).where(AgentJob.agent_id.in_(AREAS)).limit(1))
                or db.scalar(select(AgentSchedule.agent_id).where(AgentSchedule.agent_id.in_(AREAS)).limit(1))
                or db.scalar(select(Automation.id).where(Automation.kind.in_(RETIRED_AUTOMATIONS),Automation.enabled==True).limit(1))
                or db.scalar(select(Connector.id).where(Connector.id.in_(['university','university-calendar'])).limit(1)))
        if not legacy:return
        hashes={}
        for row in db.scalars(select(Item)):
            before=resource_fingerprint(row)
            row.area=AREAS.get(row.area,row.area)
            row.kind={'model':'document','material':'note'}.get(row.kind,row.kind)
            row.data=metadata(row.data or {})
            if row.source in RETIRED_SOURCES:
                row.data={**row.data,'available':False,'retired_integration':True}
            row.source=SOURCES.get(row.source,row.source)
            after=resource_fingerprint(row)
            if before!=after:hashes[(row.id,before)]=after
        for row in db.scalars(select(Item)):
            data=copy.deepcopy(row.data or {})
            for reference in data.get('source_references',[]):
                if isinstance(reference,dict):
                    value=hashes.get((reference.get('id'),reference.get('content_hash')))
                    if value:reference['content_hash']=value
            row.data=data
        for row in db.scalars(select(Inbox)):row.area=AREAS.get(row.area,row.area)
        for row in db.scalars(select(AgentJob)):
            row.agent_id=AREAS.get(row.agent_id,row.agent_id)
            for field in ['actions','sources','context_sources','execution']:
                setattr(row,field,metadata(getattr(row,field)))
            if row.status in {'pending','running','waiting_information','waiting_approval','retrying'}:
                row.status='cancelled';row.lease_id=None;row.finished_at=now()
                row.error='La actualización del espacio canceló esta ejecución. Inicia una nueva solicitud con las herramientas actuales.'
        for row in list(db.scalars(select(AgentSchedule))):
            if row.agent_id in AREAS:
                current=db.get(AgentSchedule,(row.user_id,AREAS[row.agent_id]))
                if current:db.delete(row)
                else:row.agent_id=AREAS[row.agent_id];row.enabled=False
        retired=set()
        for row in db.scalars(select(Automation)):
            if row.kind in RETIRED_AUTOMATIONS:row.enabled=False;retired.add(row.id)
        for row in db.scalars(select(Run).where(Run.automation_id.in_(retired),Run.status.in_(['pending','running']))):
            row.status='cancelled';row.finished_at=now();row.result='Integración retirada; conserva los archivos existentes.'
        for row in db.scalars(select(Chat)):row.sources=metadata(row.sources)
        db.execute(delete(Connector).where(Connector.id.in_(['university','university-calendar'])))
        db.flush()
