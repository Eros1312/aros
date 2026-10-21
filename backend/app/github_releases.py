"""Read-only release radar: five fixed public repositories, no tokens or installs."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,StrictBool
from sqlalchemy import select,update
from .database import Automation,Connector,Inbox,Item,Run,User,get_db,now
from .security import current_user,audit

CONTROL='github-releases'
SOURCE='github-release'
INTERVAL_MINUTES=720
REPOS=[('openclaw/openclaw','OpenClaw'),('fastapi/fastapi','FastAPI'),('vercel/next.js','Next.js'),('pydantic/pydantic','Pydantic'),('pymupdf/PyMuPDF','PyMuPDF')]
DOCS='https://docs.github.com/en/rest/releases/releases#get-the-latest-release'
router=APIRouter(prefix='/api/v1/github-releases',tags=['Radar de proyectos'])
UserDep=Annotated[User,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]


def ensure_github_releases(db,user,enabled=False):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    row=db.get(Connector,(CONTROL,user.id))
    if not row:
        row=Connector(id=CONTROL,user_id=user.id,name='Radar de proyectos',status='connected' if enabled else 'paused',sync_details={});db.add(row)
    schedule=db.scalar(select(Automation).where(Automation.user_id==user.id,Automation.kind=='github_releases'))
    if not schedule:
        schedule=Automation(user_id=user.id,title='Radar de publicaciones oficiales',kind='github_releases',enabled=enabled,interval_minutes=INTERVAL_MINUTES,next_run=now());db.add(schedule)
    db.flush();return row,schedule


def _public(row,schedule,active=None):
    state=row.sync_details or {} if row else {}
    observed={item['repo']:item for item in state.get('sources',[]) if isinstance(item,dict) and item.get('repo')}
    return {'enabled':bool(schedule and schedule.enabled),'connected':bool(row and row.status not in ['disconnected','revoked']),
            'status':row.status if row else 'not_configured','running':active=='running' or bool(row and row.status=='syncing'),'queued':active=='pending',
            'last_attempt_at':state.get('last_attempt_at'),'last_successful_sync_at':row.last_sync if row else None,
            'next_refresh':max(schedule.next_run,state.get('next_allowed_at') or '') if schedule and schedule.enabled else None,
            'next_allowed_at':state.get('next_allowed_at'),'outcome':state.get('outcome','never'),'error':row.error if row else None,
            'sources':[{'repo':repo,'name':name,'url':'https://github.com/'+repo,**observed.get(repo,{'state':'not_checked'})} for repo,name in REPOS],
            'scope':'Publicaciones estables públicas. No instala ni actualiza software.','documentation_url':DOCS}


@router.get('')
def radar_status(user:UserDep,db:DbDep):
    row=db.get(Connector,(CONTROL,user.id));schedule=db.scalar(select(Automation).where(Automation.user_id==user.id,Automation.kind=='github_releases'))
    active=db.scalar(select(Run.status).where(Run.user_id==user.id,Run.automation_id==schedule.id,Run.status.in_(['pending','running']))) if schedule else None
    return _public(row,schedule,active)


class RadarIn(BaseModel):
    enabled:StrictBool


@router.patch('')
def configure_radar(payload:RadarIn,user:UserDep,db:DbDep):
    row,schedule=ensure_github_releases(db,user)
    row.status='connected' if payload.enabled else 'paused';row.revision+=1;row.sync_started_at=None;row.error=None
    schedule.enabled=payload.enabled;schedule.interval_minutes=INTERVAL_MINUTES
    schedule.next_run=max(now(),(row.sync_details or {}).get('next_allowed_at') or '')
    if not payload.enabled:
        db.execute(update(Run).where(Run.user_id==user.id,Run.automation_id==schedule.id,Run.status.in_(['pending','running'])).values(status='cancelled',finished_at=now(),result='Radar pausado por el propietario.'))
    audit(db,user,'github_radar.enabled' if payload.enabled else 'github_radar.paused');db.commit()
    return _public(row,schedule)


@router.post('/disconnect')
def disconnect_radar(user:UserDep,db:DbDep):
    row,schedule=ensure_github_releases(db,user)
    row.status='disconnected';row.revision+=1;row.sync_started_at=None;schedule.enabled=False
    db.execute(update(Run).where(Run.user_id==user.id,Run.automation_id==schedule.id,Run.status.in_(['pending','running'])).values(status='cancelled',finished_at=now(),result='Radar desconectado.'))
    audit(db,user,'github_radar.disconnected');db.commit();return _public(row,schedule)


@router.post('/refresh',status_code=202)
def queue_refresh(user:UserDep,db:DbDep):
    row,schedule=ensure_github_releases(db,user)
    if row.status in ['disconnected','revoked']:raise HTTPException(409,'Activa el radar antes de comprobar publicaciones.')
    active=db.scalar(select(Run).where(Run.user_id==user.id,Run.automation_id==schedule.id,Run.status.in_(['pending','running'])))
    if active:db.commit();return {'queued':active.status=='pending','running':active.status=='running','run_id':active.id}
    if (row.sync_details or {}).get('next_allowed_at','')>now():
        db.commit();return {'queued':False,'cached':True,'next_allowed_at':row.sync_details['next_allowed_at']}
    if row.status=='syncing':raise HTTPException(409,'El radar ya está comprobando publicaciones.')
    pending=db.scalar(select(Run.id).where(Run.user_id==user.id,Run.status.in_(['pending','running'])).limit(4).offset(3))
    if pending:raise HTTPException(429,'Ya hay varios trabajos pendientes. Espera antes de comprobar el radar.')
    run=Run(user_id=user.id,automation_id=schedule.id,origin='manual');db.add(run);audit(db,user,'github_radar.queued');db.commit()
    return {'queued':True,'run_id':run.id}


def fetch_release(client,repo,previous):
    endpoint='https://api.github.com/repos/'+repo+'/releases/latest'
    headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'Aros-public-release-radar/1'}
    etag=previous.get('etag')
    if isinstance(etag,str) and len(etag)<=512 and '\n' not in etag and '\r' not in etag:headers['If-None-Match']=etag
    with client.stream('GET',endpoint,headers=headers) as response:
        if response.status_code==304:
            if not previous.get('release') and previous.get('state')!='no_public_release':raise ValueError('304 without baseline')
            return {**previous,'repo':repo,'state':'unchanged','checked_at':now(),'http_status':304,'error':None}
        if response.status_code==404:return {'repo':repo,'state':'no_public_release','checked_at':now(),'http_status':404,'detail':'No hay una publicación estable pública accesible en este endpoint.'}
        response.raise_for_status();raw=bytearray()
        for part in response.iter_bytes():
            raw.extend(part)
            if len(raw)>1_000_000:raise ValueError('response too large')
        value=json.loads(raw)
        if not isinstance(value,dict) or not isinstance(value.get('id'),int) or value.get('draft') is not False or value.get('prerelease') is not False:raise ValueError('invalid release')
        tag=value.get('tag_name');url=value.get('html_url');published=value.get('published_at')
        if not isinstance(tag,str) or not 0<len(tag)<=160 or not isinstance(url,str):raise ValueError('invalid metadata')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc.lower()!='github.com' or not parsed.path.lower().startswith('/'+repo.lower()+'/releases/tag/') or parsed.query or parsed.fragment:raise ValueError('invalid source URL')
        instant=datetime.fromisoformat(str(published).replace('Z','+00:00'))
        if instant.tzinfo is None:raise ValueError('invalid publication time')
        release={'id':value['id'],'tag':tag,'title':str(value.get('name') or tag)[:240],'url':url,'published_at':instant.isoformat()}
        return {'repo':repo,'state':'checked','checked_at':now(),'http_status':200,'etag':response.headers.get('etag','')[:512],
                'response_sha256':hashlib.sha256(raw).hexdigest(),'release':release,'error':None}


def refresh_github_releases(db,user,manual=False):
    row,schedule=ensure_github_releases(db,user);db.commit()
    if row.status in ['disconnected','revoked'] or (not schedule.enabled and not manual):raise HTTPException(409,'El radar está pausado o desconectado.')
    previous=dict(row.sync_details or {});attempt=now()
    if previous.get('next_allowed_at','')>attempt:return {'outcome':'cached','created':0,'next_allowed_at':previous['next_allowed_at']}
    if row.status=='syncing':raise HTTPException(409,'El radar ya está comprobando publicaciones.')
    revision=row.revision
    claimed=db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision,Connector.status==row.status).values(status='syncing',revision=revision+1,sync_started_at=attempt));db.commit()
    if claimed.rowcount!=1:raise HTTPException(409,'La configuración del radar cambió.')
    revision+=1;prior={x['repo']:x for x in previous.get('sources',[]) if isinstance(x,dict) and x.get('repo')};sources=[]
    next_allowed=(datetime.now(timezone.utc)+timedelta(minutes=INTERVAL_MINUTES)).isoformat()
    with httpx.Client(timeout=httpx.Timeout(10,connect=5),follow_redirects=False,trust_env=False) as client:
        for repo,_ in REPOS:
            # Recheck after each response so pause/revocation stops the next GET.
            live=db.scalar(select(Connector.revision).where(Connector.id==CONTROL,Connector.user_id==user.id));db.commit()
            if live!=revision:raise HTTPException(409,'El radar se pausó o cambió durante la consulta.')
            try:sources.append(fetch_release(client,repo,prior.get(repo,{})))
            except Exception as exc:
                status=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
                code='rate_limited' if status in (403,429) else 'network_error' if isinstance(exc,httpx.RequestError) else 'format_or_access_error'
                sources.append({**prior.get(repo,{}),'repo':repo,'state':'error','checked_at':now(),'error_code':code,'error':'No se pudo comprobar esta fuente; se conserva el último resultado.','http_status':status})
                if status in (403,429):
                    remaining=REPOS[len(sources):]
                    sources.extend({**prior.get(r,{}),'repo':r,'state':'deferred','error':'Consulta aplazada por el límite de GitHub.'} for r,_ in remaining)
                    retry=exc.response.headers.get('retry-after','')
                    if retry.isdigit():next_allowed=max(next_allowed,(datetime.now(timezone.utc)+timedelta(seconds=min(int(retry),86400))).isoformat())
                    break
    changed=new_count=0
    for source in sources:
        release=source.get('release');old=prior.get(source['repo'],{})
        if source['state'] not in ['checked','unchanged','no_public_release']:continue
        source['last_successful_sync_at']=attempt
        source['baseline_established']=True
        if not release:continue
        external=source['repo']+':'+str(release['id'])
        existing=db.scalar(select(Item).where(Item.user_id==user.id,Item.source==SOURCE,Item.external_id==external))
        data={'url':release['url'],'repo':source['repo'],'tag':release['tag'],'published_at':release['published_at'],
              'provenance':'GitHub REST API pública · releases/latest','license_note':'La licencia y requisitos del proyecto se comprueban en su repositorio antes de usarlo.','documentation_url':DOCS}
        title=source['repo'].split('/')[-1]+' · '+release['tag'];description='Publicación estable del proyecto. Revisa las notas oficiales y los requisitos antes de decidir si te interesa. Aros no instala ni actualiza software.'
        if not existing:
            existing=Item(user_id=user.id,source=SOURCE,external_id=external,kind='news',area='technology',title=title,description=description,status='active',data=data);db.add(existing);db.flush();new_count+=1
            if old.get('baseline_established'):
                db.add(Inbox(user_id=user.id,item_id=existing.id,title='Nueva publicación: '+title,area='technology',reason='La fuente oficial ha publicado una versión distinta desde la última comprobación.'));changed+=1
        elif existing.data!=data or existing.title!=title:
            db.execute(update(Item).where(Item.id==existing.id,Item.version==existing.version).values(data=data,title=title,version=Item.version+1,updated_at=attempt))
    failed=any(s['state'] in ['error','deferred'] for s in sources);usable=any(s['state'] in ['checked','unchanged','no_public_release'] for s in sources)
    outcome='error' if not usable else 'partial' if failed else 'updated' if new_count else 'no_changes'
    state={'outcome':outcome,'last_attempt_at':attempt,'next_allowed_at':next_allowed,'sources':sources,'created':new_count,'notifications':changed}
    finished=db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status=('error' if failed else 'connected') if schedule.enabled else 'paused',sync_started_at=None,sync_details=state,last_sync=attempt if not failed else row.last_sync,error='Algunas fuentes no se pudieron comprobar.' if failed else None))
    if finished.rowcount!=1:db.rollback();raise HTTPException(409,'El radar cambió de estado; no se guardaron novedades.')
    schedule.next_run=next_allowed;audit(db,user,'github_radar.'+outcome);db.commit();return state
