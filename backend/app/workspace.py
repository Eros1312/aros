"""Owner-scoped workspace queries: priorities, collections and provenance."""
from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, or_, update, cast, String
from sqlalchemy.orm import defer, load_only
from .database import Item, Inbox, Connector, AgentJob, User, get_db, now
from .security import current_user, audit
from .schemas import Area, ItemOut
from .services import item_dict, owned_item
from .source_access import visible_items_clause
from .source_access import resource_fingerprint
from .ai import normalized
from .calendar import event_interval

router = APIRouter(prefix='/api/v1/workspace', tags=['Workspace'])
UserDep = Annotated[User, Depends(current_user)]
DbDep = Annotated[object, Depends(get_db)]
NAMES = {'learning': 'Aprendizaje', 'events': 'Eventos', 'resources': 'Recursos',
         'technology': 'IA y tecnología', 'personal': 'Personal', 'server': 'Operación'}


class NextAction(BaseModel):
    label: str
    section: str
    item_id: str | None = None


class PriorityOut(BaseModel):
    id: str
    title: str
    reason: str
    item_id: str | None = None
    area: Area
    action: NextAction


class ProjectOut(BaseModel):
    id: str
    area: Area
    title: str
    status: str
    next_action: str
    document_count: int
    task_count: int
    href: str


class ResultOut(BaseModel):
    id: str
    title: str
    area: Area
    created_at: str
    artifact_id: str
    job_id: str | None = None
    section: str


class CoverageOut(BaseModel):
    id: str
    name: str
    status: str
    last_successful_sync_at: str | None = None
    error: str | None = None


class AttentionOut(BaseModel):
    actionable_count: int
    excluded_non_actionable_count: int


class WorkspaceSummary(BaseModel):
    generated_at: str
    timezone: str
    priorities: list[PriorityOut]
    projects: list[ProjectOut]
    results: list[ResultOut]
    events: list[ItemOut]
    coverage: list[CoverageOut]
    attention: AttentionOut


class LibraryMetadata(BaseModel):
    group: str
    category: str
    review_state: str
    original: bool
    indexed: bool
    partial: bool


class LibraryRecord(ItemOut):
    library: LibraryMetadata


class LibraryGroup(BaseModel):
    area: Area
    label: str
    count: int


class Pagination(BaseModel):
    offset: int
    limit: int
    total: int
    next_offset: int | None


class LibraryOut(BaseModel):
    items: list[LibraryRecord]
    groups: list[LibraryGroup]
    pagination: Pagination


class ProjectConversation(BaseModel):
    id: str
    agent_id: str
    thread_id: str
    prompt: str
    status: str
    created_at: str


class ProjectLinks(BaseModel):
    resources_total: int
    tasks_total: int
    results_total: int
    conversations_total: int
    limit: int
    truncated: bool


class ProjectDetail(BaseModel):
    project: ItemOut
    resources: list[ItemOut]
    tasks: list[ItemOut]
    results: list[ResultOut]
    conversations: list[ProjectConversation]
    links: ProjectLinks
    generated_at: str
    timezone: str


def verified_results(db, user, rows):
    """Expose integrated outputs only when a current receipt proves the record.

    Selecting, queueing or claiming success in reply text is not verification.
    Legacy deterministic outputs retain their existing presentation contract.
    """
    candidates=[row for row in rows if row.source in ['agent-selection','agent-execution','agent-conversation','agent-review']]
    # Summary queries deliberately omit source bodies. Load only the candidate
    # output text needed to compare receipts, in one batch and without blobs.
    integrated_ids=[row.id for row in candidates if row.source in ['agent-conversation','agent-review']]
    if integrated_ids:
        list(db.scalars(select(Item).options(load_only(Item.id,Item.description)).where(
            Item.user_id==user.id,Item.id.in_(integrated_ids))))
    job_ids={str((row.data or {}).get('last_execution_id') or (row.data or {}).get('execution_id') or
                 (row.data or {}).get('job_id') or '') for row in candidates}
    jobs={job.id:job for job in db.scalars(select(AgentJob).where(
        AgentJob.user_id==user.id,AgentJob.id.in_(job_ids)))} if job_ids else {}
    from .openclaw import history_is_authorized
    authorized={}
    result=[]
    for row in candidates:
        data=row.data or {}
        job_id=data.get('last_execution_id') or data.get('execution_id') or data.get('job_id')
        if row.source in ['agent-conversation','agent-review']:
            job=jobs.get(job_id)
            expected='review.digest' if row.source=='agent-review' else 'conversation.integrated'
            if not job or (job.execution or {}).get('capability')!=expected:continue
            receipt=next((receipt for receipt in (job.execution or {}).get('artifacts',[])
                          if isinstance(receipt,dict) and receipt.get('id')==row.id),None)
            from .source_access import effect_fingerprint
            if not receipt or not receipt.get('verified') or receipt.get('version')!=row.version or receipt.get('content_hash')!=resource_fingerprint(row) or (receipt.get('effect_hash') and receipt['effect_hash']!=effect_fingerprint(row)):continue
            if job.id not in authorized:authorized[job.id]=history_is_authorized(db,user,job)
            if not authorized[job.id]:continue
        elif row.kind not in ['note','project']:
            continue
        result.append({'id':row.id,'title':row.title,'area':row.area,'created_at':row.created_at,
                       'artifact_id':row.id,'job_id':job_id,'section':'activity'})
    # Updating and verifying an older task is recent work too. Keep the public
    # creation timestamp truthful while ordering by the current record change.
    updated={row.id:row.updated_at for row in candidates}
    result.sort(key=lambda value:(updated[value['id']],value['id']),reverse=True)
    return result


def linked_project(data, project_id):
    if not isinstance(data,dict):return False
    return data.get('project_id')==project_id or (
        isinstance(data.get('project_ids'),list) and project_id in data['project_ids'])


@router.get('/projects/{project_id}', response_model=ProjectDetail)
def project_detail(project_id: str, user: UserDep, db: DbDep):
    # No names, courses or event titles are used to infer membership. Keep one
    # authoritative Item identity even when a file appears in several views.
    project=db.scalar(select(Item).options(defer(Item.blob)).where(
        Item.id==project_id,Item.user_id==user.id,Item.kind=='project',
        Item.status.notin_(['archived','cancelled','canceled','withdrawn']),
        visible_items_clause(db,user)))
    from .agent_capabilities import source_allowed
    if not project or project.source.startswith('canva') or not source_allowed(db,project,user.id,project.area,policy_checked=True,lock=False):
        raise HTTPException(404,'No se ha encontrado un proyecto disponible; las referencias Canva son materiales.')
    references={ref['id'] for ref in (project.data or {}).get('source_references',[])
                if isinstance(ref,dict) and isinstance(ref.get('id'),str)}
    rows=list(db.scalars(select(Item).options(defer(Item.blob)).where(
        Item.user_id==user.id,Item.id!=project.id,
        Item.status.notin_(['archived','cancelled','canceled','withdrawn']),
        visible_items_clause(db,user),or_(Item.id.in_(references),
            Item.data['project_id'].as_string()==project.id,
            cast(Item.data['project_ids'].as_json(),String).contains('"'+project.id+'"',autoescape=True))
        ).order_by(Item.updated_at.desc(),Item.id)))
    states={}
    related=[row for row in rows if (linked_project(row.data,project.id) or row.id in references)
             and source_allowed(db,row,user.id,row.area,policy_checked=True,connector_states=states,lock=False)]
    tasks=[row for row in related if row.kind in ['task','reminder']]
    resources=[row for row in related if row.kind not in ['task','reminder']]
    results=verified_results(db,user,related)
    from .openclaw import history_is_authorized
    conversations=[]
    # SQL selects explicit run links before bounding the result, so unrelated
    # recent work cannot hide an older project's conversation.
    run_ids={(row.data or {}).get(key) for row in [project,*related]
             for key in ['last_execution_id','execution_id','job_id']}
    query=select(AgentJob).where(AgentJob.user_id==user.id,AgentJob.is_review==False,
        or_(AgentJob.id.in_(run_ids-{None}),
            cast(AgentJob.execution['command']['item_ids'].as_json(),String).contains('"'+project.id+'"',autoescape=True),
            AgentJob.execution['scope']['project_id'].as_string()==project.id))
    jobs=list(db.scalars(query.order_by(AgentJob.created_at.desc(),AgentJob.id.desc())))
    for job in jobs:
        execution=job.execution or {}
        if execution.get('chat_cleared_at') or not history_is_authorized(db,user,job):continue
        # contains above is only a portable SQL prefilter; exact membership is
        # checked again rather than treating a partial JSON substring as a link.
        command=execution.get('command') or {};scope=execution.get('scope') or {}
        if job.id not in run_ids and project.id not in command.get('item_ids',[]) and scope.get('project_id')!=project.id:continue
        conversations.append({'id':job.id,'agent_id':job.agent_id,'thread_id':job.thread_id,
                              'prompt':job.prompt,'status':job.status,'created_at':job.created_at})
    counts=[len(resources),len(tasks),len(results),len(conversations)];limit=100
    return {'project':item_dict(project),'resources':[item_dict(row) for row in resources[:limit]],
            'tasks':[item_dict(row) for row in tasks[:limit]],'results':results[:limit],
            'conversations':conversations[:limit],
            'links':dict(zip(['resources_total','tasks_total','results_total','conversations_total'],counts),
                         limit=limit,truncated=any(count>limit for count in counts)),
            'generated_at':now(),'timezone':'Europe/Madrid'}


def timestamp(value):
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError, AttributeError, TypeError, OverflowError):
        return None


def collection_metadata(item):
    data = item.data or {}
    if item.area == 'learning':
        group = str(data.get('course_title') or 'Tema sin clasificar')[:240]
        category = str(data.get('material_type') or data.get('category') or 'document')[:80]
    elif item.area == 'events':
        group = str(data.get('event_title') or data.get('event_name') or 'Evento sin confirmar')[:240]
        category = str(data.get('category') or 'document')[:80]
    else:
        group = NAMES.get(item.area, item.area)
        category = str(data.get('category') or item.kind)[:80]
    return {'group': group, 'category': category,
            'review_state': str(data.get('review_state') or 'unconfirmed'),
            'original': bool(data.get('sha256')) and item.source in ['events-file', 'upload'],
            'indexed': bool(data.get('indexed') or data.get('extracted')),
            'partial': bool(data.get('partial'))}


@router.get('/summary', response_model=WorkspaceSummary)
def summary(user: UserDep, db: DbDep):
    clock = datetime.now(timezone.utc)
    rows = list(db.scalars(select(Item).options(load_only(
        Item.id, Item.title, Item.area, Item.kind, Item.status, Item.priority, Item.due_at,
        Item.data, Item.version, Item.created_at, Item.updated_at, Item.source, Item.is_demo,
    )).where(Item.user_id == user.id, Item.status.notin_(['archived', 'cancelled']),
             visible_items_clause(db, user))))
    actionable = [i for i in rows if i.kind in ['task', 'reminder'] and i.status != 'done']
    actionable.sort(key=lambda i: (
        0 if i.priority == 'urgent' else 1 if i.priority == 'high' else 2,
        timestamp(i.due_at) or datetime.max.replace(tzinfo=timezone.utc), i.id,
    ))
    priorities = []
    for i in actionable[:3]:
        due = timestamp(i.due_at)
        reason = ('El plazo ha pasado.' if due and due < clock else
                  'Tiene una fecha próxima.' if due and due <= clock + timedelta(days=7) else
                  'Marcada con prioridad alta.' if i.priority in ['high', 'urgent'] else 'Tarea pendiente.')
        priorities.append({'id': i.id, 'title': i.title, 'reason': reason, 'item_id': i.id,
                           'area': i.area, 'action': {'label': 'Revisar', 'section': i.area, 'item_id': i.id}})
    by_id = {i.id: i for i in rows}
    alerts = list(db.scalars(select(Inbox).where(Inbox.user_id == user.id,
                                               Inbox.state.in_(['new', 'seen']))))
    actionable_alerts = [a for a in alerts if a.item_id in by_id and
                         by_id[a.item_id].kind in ['task', 'reminder'] and by_id[a.item_id].status != 'done']
    projects = []
    for area in NAMES:
        group = [i for i in rows if i.area == area]
        if not group:
            continue
        tasks = [i for i in actionable if i.area == area]
        projects.append({'id': area, 'area': area, 'title': NAMES[area], 'status': 'active',
                         'next_action': tasks[0].title if tasks else 'Consultar el material guardado',
                         'document_count': sum(i.kind == 'document' for i in group),
                         'task_count': len(tasks), 'href': '#' + area})
    results = verified_results(db,user,rows)[:6]
    upcoming = []
    for i in rows:
        if i.kind != 'event' or not i.due_at:
            continue
        interval = event_interval(i, ZoneInfo('Europe/Madrid'))
        if interval and interval['end'] > clock and interval['start'] < clock + timedelta(days=14):
            upcoming.append((interval['start'], i))
    upcoming.sort(key=lambda pair: (pair[0], pair[1].id))
    connectors = list(db.scalars(select(Connector).where(Connector.user_id == user.id,
                                                        Connector.id.notlike(r'\_%', escape='\\'))))
    coverage = [{'id': c.id, 'name': c.name, 'status': c.status,
                 'last_successful_sync_at': c.last_sync, 'error': c.error}
                for c in connectors if c.id in ['gmail', 'calendar', 'canva', 'weather', 'rss']]
    return {'generated_at': clock.isoformat(), 'timezone': 'Europe/Madrid',
            'priorities': priorities, 'projects': projects, 'results': results,
            'events': [item_dict(i) for _, i in upcoming[:6]], 'coverage': coverage,
            'attention': {'actionable_count': len(actionable),
                          'excluded_non_actionable_count': len(alerts) - len(actionable_alerts)}}


def folded(column):
    value = func.lower(column)
    for a, b in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u'), ('ü', 'u'), ('ñ', 'n')]:
        value = func.replace(value, a, b)
    return value


@router.get('/library', response_model=LibraryOut)
def library(user: UserDep, db: DbDep, area: Area | None = None,
            q: str = Query(default='', max_length=200), category: str = Query(default='', max_length=80),
            course: str = Query(default='', max_length=240), approved: bool = False,
            limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000)):
    query = select(Item).options(defer(Item.blob)).where(
        Item.user_id == user.id, Item.status.notin_(['archived', 'cancelled']),
        Item.kind.in_(['document', 'note', 'model', 'project']), visible_items_clause(db, user),
    )
    if area:
        query = query.where(Item.area == area)
    if category:
        query = query.where(Item.data['category'].as_string() == category)
    if course:
        query = query.where(Item.data['course_title'].as_string() == course)
    if approved:
        query = query.where(Item.data['review_state'].as_string() == 'approved')
    if q.strip():
        needle = normalized(q.strip()).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = '%' + needle + '%'
        query = query.where(or_(folded(Item.title).like(pattern, escape='\\'),
                               folded(Item.description).like(pattern, escape='\\'),
                               folded(Item.data['course_title'].as_string()).like(pattern, escape='\\')))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Item.updated_at.desc(), Item.id).offset(offset).limit(limit)))
    result = []
    for i in rows:
        item = item_dict(i)
        content = i.description or ''
        pos = normalized(content).find(normalized(q.strip())) if q.strip() else 0
        start = max(0, pos - 60)
        item['description'] = content[start:start + 320]
        item['library'] = collection_metadata(i)
        result.append(item)
    group_query = select(Item).options(load_only(Item.id, Item.area, Item.kind, Item.data, Item.source)).where(
        Item.user_id == user.id, Item.kind == 'document', Item.status.notin_(['archived', 'cancelled']),
        visible_items_clause(db, user),
    )
    if area:
        group_query = group_query.where(Item.area == area)
    counts = {}
    for i in db.scalars(group_query):
        group = collection_metadata(i)['group']
        key = (i.area, group)
        counts[key] = counts.get(key, 0) + 1
    return {'items': result, 'groups': [{'area': a, 'label': g, 'count': n} for (a, g), n in sorted(counts.items())],
            'pagination': {'offset': offset, 'limit': limit, 'total': total,
                           'next_offset': offset + len(rows) if offset + len(rows) < total else None}}
