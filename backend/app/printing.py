"""Personal 3D discovery with public publisher feeds and explicit verification receipts."""
import hashlib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool
from sqlalchemy import select, update, func, or_
from sqlalchemy.orm import defer

from .database import Automation, Connector, Inbox, Item, Run, User, get_db, now
from .security import current_user, audit
from .config import settings

TARGET = 10
INTERVAL_MINUTES = 720
CONTROL = '_printing-discovery'
SOURCE = 'printing-recommendation'
SEED_PATH = Path(__file__).with_name('printing_seed.json')
FEEDS = ['https://learn.adafruit.com/feed', 'https://blog.prusa3d.com/feed/']
CATALOG = 'https://learn.adafruit.com/api/guides.json?count=5000'
PAGE_HOSTS = {'learn.adafruit.com', 'www.printables.com', 'printables.com', 'www.thingiverse.com', 'thingiverse.com', '3d.nih.gov'}
IMAGE_HOSTS = {'cdn-learn.adafruit.com','media.printables.com','media.prusaprinters.org','cdn.thingiverse.com','3d.nih.gov'}
PROFILE = {'interests':['PRISMA · fiestas y producción de eventos', 'Decoración, señalización y expositores', 'Organización de material'],
           'printer':{'model':'Bambu Lab A1 mini', 'build_volume_mm':[180,180,180], 'confirmed':False,
                      'provenance':'Conversación anterior CHATPRIMA; pendiente de confirmar antes de preparar instrucciones de impresión'},
           'colors':{'values':['negro','blanco','rojo'], 'confirmed':False, 'provenance':'Conversación anterior CHATPRIMA'},
           'ams_lite':None}
router = APIRouter(prefix='/api/v1/printing', tags=['Personal 3D recommendations'])
UserDep = Annotated[User, Depends(current_user)]
DbDep = Annotated[object, Depends(get_db)]

class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.meta={}; self.links=[]; self.text=[]; self.images=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='meta':self.meta[values.get('property') or values.get('name')]=values.get('content','')
        if tag=='a' and values.get('href'):self.links.append(values['href'])
        if tag=='img' and values.get('src'):self.images.append(values['src'])
    def handle_data(self,data):self.text.append(data)

def canonical_url(url):
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in PAGE_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):return None
        if parsed.hostname.endswith('printables.com') and not re.match(r'^/model/[0-9]+',parsed.path):return None
        if parsed.hostname.endswith('thingiverse.com') and not re.match(r'^/thing:[0-9]+',parsed.path):return None
        if parsed.hostname=='3d.nih.gov' and not re.match(r'^/entries/3DPX-[0-9]+$',parsed.path):return None
        if parsed.hostname=='learn.adafruit.com' and parsed.path in ('','/'):return None
        return urlunsplit(('https',parsed.netloc,parsed.path.rstrip('/'),'',''))
    except ValueError:return None

def valid_image(url):
    try:
        parsed=urlsplit(url)
        return (parsed.scheme=='https' and parsed.hostname in IMAGE_HOSTS and not parsed.username and not parsed.password
                and parsed.port in (None,443) and len(url)<4096 and not re.search(r'(?:logo|favicon)',parsed.path,re.I))
    except (ValueError,TypeError):return False

def fetch_public(client,url,limit=2_000_000):
    with client.stream('GET',url) as response:
        if 300<=response.status_code<400:raise ValueError('El proveedor redirige el recurso')
        response.raise_for_status(); chunks=[];size=0
        for chunk in response.iter_bytes():
            size+=len(chunk)
            if size>limit:raise ValueError('La respuesta supera el límite de lectura')
            chunks.append(chunk)
        return b''.join(chunks)

def seed_candidates():
    if not SEED_PATH.exists():return []
    data=json.loads(SEED_PATH.read_text())
    return data if isinstance(data,list) else data.get('items',[])

def validate_candidate(candidate):
    if not isinstance(candidate,dict):return None
    url=canonical_url(candidate.get('source_url') or '')
    pictures=candidate.get('image_urls') or [candidate.get('image_url')]
    pictures=list(dict.fromkeys(p for p in pictures if isinstance(p,str) and valid_image(p)))[:5]
    license=candidate.get('license'); verified=candidate.get('verified_at')
    # A feed hit alone is not a verified model. Every recommendation needs a
    # publisher receipt for model license and actual model imagery.
    known=candidate.get('license_verified') is True
    unknown=candidate.get('license_state')=='unknown' and license=='No indicada; comprobar en la fuente'
    if not url or (not pictures and not candidate.get('files_evidence')) or not isinstance(license,str) or not license.strip() or not verified or not (known or unknown):
        return None
    try:
        if datetime.fromisoformat(str(verified).replace('Z','+00:00')).tzinfo is None:return None
    except ValueError:return None
    candidate=dict(candidate)
    for field in ['published_at','provider_updated_at']:
        try:
            value=datetime.fromisoformat(str(candidate.get(field) or '').replace('Z','+00:00'))
            candidate[field]=value.isoformat() if value.tzinfo else None
        except ValueError:candidate[field]=None
    if not candidate.get('title') or not candidate.get('usage'):return None
    return {**candidate,'source_url':url,'image_urls':pictures,'image_url':pictures[0] if pictures else None,
            'title':str(candidate['title'])[:240], 'usage':str(candidate['usage'])[:1500],
            'description':str(candidate.get('description') or '')[:4000], 'license':license[:240]}

def profile_score(candidate):
    from .ai import normalized
    text=normalized(' '.join(str(candidate.get(key) or '') for key in ['title','description','usage','profile_tags']))
    groups=[('química',['molecule','molecular','protein','chemical','chemistry','lab','pipette','tube','quimica']),
            ('PRISMA',['event','badge','sign','light','lighting','led','neopixel','display','audio','sound','speaker','evento','cartel']),
            ('tecnología',['sensor','raspberry','electronics','robot','camera','keyboard','tech']),
            ('organización',['organizer','holder','stand','tool','rack','organizador','soporte'])]
    tags=[name for name,words in groups if any(re.search(r'\b'+re.escape(word)+r'(?:s)?\b',text) for word in words)]
    # Affinity is deterministic, never a printer compatibility claim.
    party_words=['party','fiesta','decoration','decoracion','decorative','ornament','display','badge','sign','cartel','qr','coaster','table','pulsera','wristband','event','evento','lantern','light','led']
    party=sum(bool(re.search(r'\b'+re.escape(word)+r'(?:s)?\b',text)) for word in party_words)
    if party and 'PRISMA' not in tags:tags.insert(0,'PRISMA')
    penalty=any(word in text for word in ['calibration','calibracion','gearbox','engranaje','pipette','probeta'])
    return party*5+('organización' in tags)*2+(len(tags) if party or 'organización' in tags else 0)-(10 if penalty else 0), tags

def verify_page(client,url):
    url=canonical_url(url)
    if not url:return None
    is_adafruit=urlsplit(url).hostname=='learn.adafruit.com'
    raw=fetch_public(client,url+'?view=all' if is_adafruit else url).decode('utf-8',errors='replace'); parser=PageMetadata();parser.feed(raw)
    license=None;license_url=None
    # Guide-text or site-footer licenses are not licenses for printable files.
    if is_adafruit:
        # The publisher must actually offer printable design files in this guide.
        if not re.search(r'(?:\.stl\b|\.3mf\b|download\s+(?:STLs|3D\s+files)|printables\.com/model/|thingiverse\.com/thing:)',raw,re.I):return None
        title=parser.meta.get('og:title');description=parser.meta.get('og:description') or parser.meta.get('description') or ''
        if not title:return None
        if re.match(r'^(?:Skill Badge Guide|How to|Getting Started)',title,re.I):return None
        score,tags=profile_score({'title':title,'description':description})
        if not score:return None
        images=[parser.meta.get('og:image')]+[urljoin(url,image) for image in parser.images if 'cdn-learn.adafruit.com/assets/' in image]
        images=[image for image in images if valid_image(image)]
        uses={'química':'Apoyo didáctico o prototipos de medición para tus estudios de química e ingeniería química.',
              'PRISMA':'Iluminación, señalización o accesorios para preparar proyectos y eventos de PRISMA.',
              'tecnología':'Montaje y protección de proyectos electrónicos, sensores o herramientas de tecnología.',
              'organización':'Organizar o sujetar herramientas y componentes en tu mesa de trabajo.'}
        usage=' '.join(uses[tag] for tag in tags)+(' Función descrita por el autor: '+description if description else '')
        return validate_candidate({'title':title,'description':description,'source_url':url,'image_urls':images,
                                   'license':'No indicada; comprobar en la fuente','license_url':url,'license_state':'unknown',
                                   'license_verified':False,'source':'Adafruit Learning System','verified_at':now(),
                                   'usage':usage,'profile_tags':tags,'published_at':parser.meta.get('article:published_time'),'provider_updated_at':parser.meta.get('article:modified_time'),'author':parser.meta.get('author'),'image_kind':'publisher_image',
                                   'files_evidence':'La guía del autor incluye archivos STL/3MF o un enlace a sus modelos.',
                                   'commercial_use':'No autorizado por esta recomendación; comprobar licencia del archivo.'})
    title=parser.meta.get('og:title')
    # Only structured model metadata can establish file rights. A generic CC
    # link anywhere on a page (including an Article/WebSite) proves nothing.
    for script in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',raw,re.I|re.S):
        try:document=json.loads(script)
        except (ValueError,TypeError):continue
        documents=document if isinstance(document,list) else [document]
        for node in documents:
            if isinstance(node,dict) and isinstance(node.get('@graph'),list):documents.extend(node['@graph'])
            if not isinstance(node,dict) or node.get('@type')!='3DModel' or node.get('name')!=title:continue
            license_ref=node.get('license')
            known_cc=r'https://creativecommons\.org/(?:licenses/(?:by|by-sa|by-nd|by-nc|by-nc-sa|by-nc-nd)/(?:1\.0|2\.0|2\.5|3\.0|4\.0)|publicdomain/(?:zero|mark)/1\.0)/?'
            if isinstance(license_ref,str) and re.fullmatch(known_cc,license_ref,re.I):
                license_url=license_ref.rstrip('/');license=license_url.replace('https://creativecommons.org/','');break
        if license:break
    if not license:return None
    image=parser.meta.get('og:image');title=parser.meta.get('og:title')
    description=parser.meta.get('og:description') or parser.meta.get('description') or ''
    if not title:return None
    score,tags=profile_score({'title':title,'description':description})
    if not score:return None
    usage='Aplicación sugerida para '+', '.join(tags)+'. Revisa la ficha del autor para dimensiones, montaje y materiales.'
    return validate_candidate({'title':title,'description':description,'source_url':url,'image_url':urljoin(url,image) if image else None,'files_evidence':'Ficha del autor identificada como 3DModel en metadatos públicos.',
                               'license':license,'license_url':license_url,'license_verified':True,'source':urlsplit(url).hostname,
                               'verified_at':now(),'usage':usage,'profile_tags':tags,'published_at':parser.meta.get('article:published_time'),'provider_updated_at':parser.meta.get('article:modified_time'),'author':parser.meta.get('author'),
                               'image_kind':'publisher_model_image'})

def source_error(exc):
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        return ('rate_limited' if code==429 else 'access_blocked' if code in (401,403) else 'not_found' if code==404 else 'http_error')
    return 'timeout' if isinstance(exc,httpx.TimeoutException) else 'format_changed' if isinstance(exc,(ValueError,TypeError,KeyError,ElementTree.ParseError)) else 'network_error'


def discover_candidates(excluded_urls=None,cursor=0):
    """Public endpoints only. Recheck existing entries; bootstrap is not freshness."""
    candidates=[];links={};sources=[]
    with httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False,trust_env=False) as client:
        for endpoint in [*FEEDS,CATALOG]:
            identifier='adafruit-catalog' if endpoint==CATALOG else 'adafruit-feed' if 'adafruit' in endpoint else 'prusa-feed'
            report={'id':identifier,'endpoint':endpoint,'state':'success','checked_at':now(),'fetched':0,'candidates':0,'verified':0,'failed':0,'cursor':int(cursor or 0)}
            sources.append(report)
            try:
                raw=fetch_public(client,endpoint,6_000_000 if endpoint==CATALOG else 2_000_000)
                report['response_sha256']=hashlib.sha256(raw).hexdigest()
                if endpoint==CATALOG:
                    document=json.loads(raw)
                    if not isinstance(document,dict) or not isinstance(document.get('guides'),list):raise ValueError('catalog schema')
                    entries=document['guides'][:5000];report['fetched']=len(entries)
                    for wrapper in entries:
                        guide=wrapper.get('guide',{})
                        url=canonical_url(guide.get('guide_url',''))
                        title=str(guide.get('title',''));description=str(guide.get('description',''))
                        if url and re.search(r'3d.print|printable|enclosure|decoration|ornament',title+' '+description,re.I):
                            links.setdefault(url,{'sources':set(),'title':title,'description':description,'author':guide.get('author',{}).get('name') if isinstance(guide.get('author'),dict) else None})['sources'].add(identifier)
                else:
                    tree=ElementTree.fromstring(raw);ns={'a':'http://www.w3.org/2005/Atom'}
                    if tree.tag.split('}')[-1] not in ('rss','feed','RDF'):raise ValueError('feed schema')
                    entries=tree.findall('.//item') or tree.findall('a:entry',ns);report['fetched']=len(entries)
                    for entry in entries[:60]:
                        title=entry.findtext('title') or entry.findtext('a:title',namespaces=ns) or ''
                        chunks=[entry.findtext('description') or '',entry.findtext('a:content',namespaces=ns) or '',entry.findtext('a:summary',namespaces=ns) or '']
                        if not re.search(r'3d.print|printable|\.stl|enclosure|decoration|ornament|party',title+' '+' '.join(chunks),re.I):continue
                        parser=PageMetadata();parser.feed(' '.join(chunks))
                        urls=parser.links[:100]
                        element=entry.find('a:link',ns)
                        urls.append(entry.findtext('link') or (element.get('href') if element is not None else None))
                        for link in urls:
                            url=canonical_url(link or '')
                            if url:links.setdefault(url,{'sources':set(),'title':title,'description':' '.join(parser.text)})['sources'].add(identifier)
            except Exception as exc:
                report.update(state='error',failed=1,error_code=source_error(exc),error='No se pudo comprobar esta fuente; se conserva su último contenido válido.')
                if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code==429:
                    retry=exc.response.headers.get('Retry-After','')
                    if retry.isdigit():report['retry_after_seconds']=min(int(retry),86400)
        # Highest event relevance first; deterministic rotating window avoids
        # repeatedly checking just the original ten or skipping existing updates.
        pending=sorted(links,key=lambda url:(-profile_score(links[url])[0],url))
        if pending:
            start=int(cursor or 0)%len(pending);pending=pending[start:]+pending[:start]
        consumed=0;deadline=time.monotonic()+120
        for url in pending[:30]:
            if time.monotonic()>deadline:break
            consumed+=1
            owners=[r for r in sources if r['id'] in links[url]['sources']]
            for report in owners:report['candidates']+=1
            try:
                candidate=verify_page(client,url)
                if candidate:
                    if not candidate.get('author') and links[url].get('author'):candidate['author']=str(links[url]['author'])[:240]
                    candidates.append(candidate)
                    for report in owners:report['verified']+=1
            except Exception as exc:
                for report in owners:
                    report.update(state='partial',error_code=source_error(exc),error='Algunas fichas no pudieron comprobarse.');report['failed']+=1
        for report in sources:
            if report['state']=='success':report['last_successful_sync_at']=report['checked_at']
            report['rejected']=max(0,report['candidates']-report['verified']-report['failed'])
            report['cursor']=int(cursor or 0)+consumed if report['state']!='error' else int(cursor or 0)
    return {'candidates':candidates,'sources':sources,'cursor':int(cursor or 0)+consumed,'checked':consumed}


def discovery_result(value,cursor):
    # Legacy adapter shape remains supported for existing offline/provider doubles.
    if isinstance(value,dict):return value
    candidates,errors=value
    return {'candidates':candidates,'sources':[{'id':'legacy-adapter','state':'error' if errors else 'success','checked_at':now(),'fetched':len(candidates),'candidates':len(candidates),'verified':len(candidates),'failed':len(errors),'error':errors[0] if errors else None,'error_code':'provider_error' if errors else None}], 'cursor':cursor,'checked':len(candidates)}

SOURCE_FIELDS={'title','description','usage','source_url','image_url','image_urls','license','license_url','license_state','license_verified','source','profile_tags','published_at','provider_updated_at','author','cost','files_evidence','image_kind','commercial_use'}
def source_payload(candidate):return {key:candidate.get(key) for key in SOURCE_FIELDS}
def payload_hash(candidate):return hashlib.sha256(json.dumps(source_payload(candidate),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def ensure_printing(db,user):
    row=db.get(Connector,(CONTROL,user.id))
    if not row:
        row=Connector(id=CONTROL,user_id=user.id,name='Descubrimiento personal 3D',status='connected',sync_details={});db.add(row)
    automation=db.scalar(select(Automation).where(Automation.user_id==user.id,Automation.kind=='printing_discovery'))
    if not automation:
        automation=Automation(user_id=user.id,title='Actualizar el catálogo 3D',kind='printing_discovery',enabled=False,interval_minutes=INTERVAL_MINUTES,next_run=now());db.add(automation)
    if automation.title=='Descubrir 10 modelos 3D nuevos':automation.title='Actualizar el catálogo 3D'
    db.flush();return row,automation

def refresh_printing(db,user,manual=False):
    row,automation=ensure_printing(db,user);db.commit()
    if not automation.enabled and not manual:raise HTTPException(409,'La búsqueda automática está pausada.')
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    if row.status=='syncing' and row.sync_started_at and row.sync_started_at>=cutoff:raise HTTPException(409,'Ya se están buscando modelos 3D nuevos.')
    revision=row.revision;previous=dict(row.sync_details or {});attempt=now()
    claimed=db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision).values(status='syncing',revision=revision+1,sync_started_at=attempt));db.commit()
    if claimed.rowcount!=1:raise HTTPException(409,'Ya se están buscando modelos 3D nuevos.')
    revision+=1
    try:
        cursor=previous.get('cursor',0)
        result=discovery_result(discover_candidates(set(),cursor),cursor)
        sources=result.get('sources',[]);prior_sources={r['id']:r for r in previous.get('sources',[]) if isinstance(r,dict) and r.get('id')}
        for source in sources:
            if source['state']=='success':source['last_successful_sync_at']=attempt
            else:source['last_successful_sync_at']=prior_sources.get(source['id'],{}).get('last_successful_sync_at')
        failed=[source for source in sources if source['state']!='success']
        usable=any(source['state'] in ('success','partial') for source in sources)
        unique={};skipped=0
        for raw in result.get('candidates',[]):
            candidate=validate_candidate(raw)
            if candidate and profile_score(candidate)[0]>0:unique.setdefault(candidate['source_url'],candidate)
            else:skipped+=1
        created=updated=unchanged=0
        existing={item.external_id:item for item in db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source==SOURCE))}
        if usable:
            for url,candidate in sorted(unique.items(),key=lambda entry:(-profile_score(entry[1])[0],entry[0])):
                identity=hashlib.sha256(url.encode()).hexdigest();item=existing.get(identity);fingerprint=payload_hash(candidate)
                if item:
                    old=item.data or {};changed=old.get('source_fingerprint',payload_hash(old))!=fingerprint
                    # Archived/discarded entries stay discarded, and private notes,
                    # approvals, selections and project links are never overwritten.
                    merged={**old,**source_payload(candidate),'source_fingerprint':fingerprint,'last_checked_at':attempt,'verified_at':candidate['verified_at'],'first_discovered_at':old.get('first_discovered_at') or old.get('recommended_at') or item.created_at}
                    if changed:
                        old_title=old.get('source_title') or old.get('title');new_description=candidate['usage']+'\n\n'+candidate.get('description','')
                        item_title=candidate['title'] if item.title==old_title else item.title
                        old_description=old.get('source_description') or str(old.get('usage',''))+'\n\n'+str(old.get('description',''))
                        item_description=new_description if item.description==old_description else item.description
                        merged.update(source_title=candidate['title'],source_description=new_description)
                        changed_row=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==item.version).values(data=merged,title=item_title,description=item_description,version=item.version+1,updated_at=attempt))
                        if changed_row.rowcount!=1:raise HTTPException(409,'Una selección cambió durante la actualización; vuelve a intentarlo.')
                        updated+=1
                    else:
                        # CAS even a freshness-only write so a concurrent favorite
                        # or note is not erased by a stale JSON snapshot.
                        changed_row=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==item.version).values(data=merged))
                        if changed_row.rowcount!=1:raise HTTPException(409,'Una selección cambió durante la actualización; vuelve a intentarlo.')
                        unchanged+=1
                else:
                    description=candidate['usage']+'\n\n'+candidate.get('description','')
                    data={**candidate,'recommended_at':attempt,'first_discovered_at':attempt,'last_checked_at':attempt,'source_fingerprint':fingerprint,'source_title':candidate['title'],'source_description':description,'batch':attempt}
                    db.add(Item(user_id=user.id,source=SOURCE,external_id=identity,kind='model',area='printing',title=candidate['title'],description=description,status='active',data=data));created+=1
        outcome='error' if not usable else 'partial' if failed else 'updated' if created or updated else 'no_changes'
        errors=[r.get('error') for r in failed if r.get('error')]
        warning='Algunas fuentes fallaron; la colección anterior se conserva.' if outcome=='partial' else None
        report={'outcome':outcome,'last_attempt_at':attempt,'last_refresh':attempt,'last_successful_sync_at':attempt if not failed and usable else previous.get('last_successful_sync_at'),'discovered':created,'updated':updated,'unchanged':unchanged,'skipped':skipped,'checked':result.get('checked',0),'sources':sources,'warning':warning,'error':'No se pudo comprobar la actualización de las fuentes.' if outcome=='error' else None,'provider_errors':errors,'target':TARGET,'deficit':0,'catalog_candidates':len(unique),'interval_minutes':automation.interval_minutes,'cursor':result.get('cursor',cursor) if usable else cursor}
        report['summary']='No se pudo comprobar la actualización.' if outcome=='error' else f'{created} nuevos para ti, {updated} fichas actualizadas.' if created or updated else 'Fuentes consultadas: no hay novedades relevantes.'
        state={**report,'history':[{k:v for k,v in report.items() if k!='history'},*previous.get('history',[])][:20]}
        automation=db.scalar(select(Automation).where(Automation.id==automation.id).with_for_update().execution_options(populate_existing=True))
        if not automation.enabled and not manual:
            db.rollback();db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision).values(status='paused',sync_started_at=None));db.commit();raise HTTPException(409,'La búsqueda se pausó; se conserva la colección anterior.')
        finished=db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status=('error' if outcome=='error' else 'connected') if automation.enabled else 'paused',last_sync=report['last_successful_sync_at'],sync_started_at=None,sync_details=state,error=report['error']))
        if finished.rowcount!=1:db.rollback();raise HTTPException(409,'La búsqueda cambió de estado; vuelve a cargar las recomendaciones.')
        automation.next_run=(datetime.now(timezone.utc)+timedelta(minutes=automation.interval_minutes)).isoformat()
        if created:db.add(Inbox(user_id=user.id,title=f'{created} modelos 3D nuevos para ti',area='printing',reason='Nuevas fichas de fuentes públicas. Revisa imagen, uso y licencia antes de imprimir.'))
        audit(db,user,'printing.discovery_'+outcome,str(created));db.commit()
        return {**report,'next_refresh':automation.next_run if automation.enabled else None}
    except Exception:
        db.rollback();db.refresh(automation)
        failure={'outcome':'error','last_attempt_at':attempt,'last_refresh':attempt,'last_successful_sync_at':previous.get('last_successful_sync_at'),'discovered':0,'updated':0,'unchanged':0,'checked':0,'cursor':previous.get('cursor',0),'error':'No se pudo completar la actualización; se conserva la colección anterior.','summary':'No se pudo comprobar la actualización.','sources':[{'id':'collector','state':'error','checked_at':attempt,'error_code':'collector_error'}]}
        failure_state={**previous,**failure,'history':[failure,*previous.get('history',[])][:20]}
        db.execute(update(Connector).where(Connector.id==CONTROL,Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status='error' if automation.enabled else 'paused',error=failure['error'],sync_details=failure_state,sync_started_at=None));db.commit();raise

@router.get('/sync-history')
def sync_history(user:UserDep,db:DbDep):
    row=db.get(Connector,(CONTROL,user.id))
    return {'items':(row.sync_details or {}).get('history',[]) if row else []}

@router.get('/sources')
def printing_sources(user:UserDep,db:DbDep):
    row=db.get(Connector,(CONTROL,user.id));observed={r['id']:r for r in (row.sync_details or {}).get('sources',[]) if r.get('id')} if row else {}
    return {'items':[{**{'id':key,'endpoint':url,'state':'not_checked','access':'public_feed' if key.endswith('feed') else 'documented_public_api'},**observed.get(key,{})} for key,url in [('adafruit-feed',FEEDS[0]),('prusa-feed',FEEDS[1]),('adafruit-catalog',CATALOG)]], 'unconnected':[{'id':name,'state':'not_connected','reason':'Sin integración API autorizada; solo enlaces públicos proporcionados por las fuentes o por ti.'} for name in ['makerworld','printables','thingiverse']]}

def search_column(column):
    # Portable accent-insensitive search in PostgreSQL and SQLite, no extension.
    result=func.lower(column)
    for accented,plain in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),('ü','u'),('ñ','n')]:
        result=func.replace(result,accented,plain)
    return result

@router.get('/recommendations')
def recommendations(user:UserDep,db:DbDep,offset:int=0,limit:int=60,q:str='',category:str='',favorite:bool=False):
    if offset<0 or limit<1 or limit>100 or len(q)>120 or category not in ('','química','PRISMA','tecnología','organización'):
        raise HTTPException(422,'Revisa los filtros de la colección.')
    row,automation=ensure_printing(db,user);db.commit()
    conditions=[Item.user_id==user.id,Item.source==SOURCE,Item.status.notin_(['archived','cancelled','canceled'])]
    total=db.scalar(select(func.count()).select_from(Item).where(*conditions)) or 0
    if favorite:conditions.append(Item.data['favorite'].as_boolean()==True)
    if category:
        if db.get_bind().dialect.name=='postgresql':
            tags=func.json_array_elements_text(Item.data['profile_tags']).table_valued('value').render_derived()
        else:
            tags=func.json_each(Item.data['profile_tags']).table_valued('value')
        conditions.append(select(1).select_from(tags).where(tags.c.value==category).correlate(Item).exists())
    from .ai import normalized
    for word in normalized(q).split():
        pattern='%'+word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        conditions.append(or_(search_column(Item.title).like(pattern,escape='\\'),search_column(Item.description).like(pattern,escape='\\')))
    matched=db.scalar(select(func.count()).select_from(Item).where(*conditions)) or 0
    items=list(db.scalars(select(Item).options(defer(Item.blob)).where(*conditions).order_by(Item.created_at.desc(),Item.id.desc()).offset(offset).limit(limit)))
    result=[]
    for item in items:
        data=item.data or {}
        result.append({'id':item.id,'title':item.title,'description':data.get('description',''),'source_url':data.get('source_url'),
                       'image_url':data.get('image_url'),'image_urls':data.get('image_urls',[]),'license':data.get('license'),
                       'license_url':data.get('license_url'),'source':data.get('source'),'published_at':data.get('published_at'),
                       'license_state':data.get('license_state','verified' if data.get('license_verified') else 'unknown'),
                       'recommended_at':data.get('recommended_at'),'usage':data.get('usage'),'profile_tags':data.get('profile_tags',[]),
                       'image_kind':data.get('image_kind'),'verified_at':data.get('verified_at'),'last_checked_at':data.get('last_checked_at'),'provider_updated_at':data.get('provider_updated_at'),'first_discovered_at':data.get('first_discovered_at') or data.get('recommended_at'),'author':data.get('author'),'cost':data.get('cost'),'favorite':data.get('favorite') is True})
    active=list(db.scalars(select(Run.status).where(Run.automation_id==automation.id,Run.user_id==user.id,Run.status.in_(['pending','running']))))
    state={'outcome':'never','last_attempt_at':None,'last_successful_sync_at':None,'sources':[],'updated':0,'unchanged':0,'last_refresh':None,'discovered':0,'target':TARGET,'deficit':TARGET,**(row.sync_details or {}),'error':row.error,'next_refresh':automation.next_run if automation.enabled else None,'enabled':automation.enabled,'running':row.status=='syncing' or 'running' in active,'queued':'pending' in active,'total':total}
    profile=PROFILE if user.email==settings.admin_email else {'interests':['Tecnología','Organización y herramientas'],'printer':None,'colors':None,'ams_lite':None}
    return {'items':result,'status':state,'profile':profile,'pagination':{'offset':offset,'limit':limit,'total':matched,'next_offset':offset+len(items) if offset+len(items)<matched else None}}

class FavoriteIn(BaseModel):
    favorite: StrictBool

@router.patch('/recommendations/{item_id}/favorite')
def favorite_model(item_id:str,payload:FavoriteIn,user:UserDep,db:DbDep):
    item=db.scalar(select(Item).options(defer(Item.blob)).where(Item.id==item_id,Item.user_id==user.id,Item.source==SOURCE,Item.status.notin_(['archived','cancelled','canceled'])))
    if not item:raise HTTPException(404,'Este modelo no está en tu colección.')
    changed=db.execute(update(Item).where(Item.id==item.id,Item.user_id==user.id,Item.version==item.version).values(data={**(item.data or {}),'favorite':payload.favorite},version=item.version+1,updated_at=now()))
    if changed.rowcount!=1:
        db.rollback();raise HTTPException(409,'El modelo cambió; vuelve a cargar la colección.')
    audit(db,user,'printing.favorite_saved' if payload.favorite else 'printing.favorite_removed',item.id);db.commit()
    return {'id':item.id,'favorite':payload.favorite}

@router.post('/refresh',status_code=202)
def refresh(user:UserDep,db:DbDep):
    db.scalar(select(User).where(User.id==user.id).with_for_update())
    row,automation=ensure_printing(db,user)
    active=db.scalar(select(Run).where(Run.automation_id==automation.id,Run.user_id==user.id,Run.status.in_(['pending','running'])))
    if active:
        db.commit();return {'queued':active.status=='pending','running':active.status=='running','run_id':active.id}
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    if row.status=='syncing' and row.sync_started_at and row.sync_started_at>=cutoff:
        db.commit();return {'queued':False,'running':True,'run_id':None}
    run=Run(user_id=user.id,automation_id=automation.id,origin='manual');db.add(run)
    audit(db,user,'printing.discovery_queued',automation.id);db.commit()
    return {'queued':True,'running':False,'run_id':run.id}
