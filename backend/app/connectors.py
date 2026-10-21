import base64, hashlib, json, secrets, re, socket, ipaddress
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse
import httpx
from cryptography.fernet import Fernet
from defusedxml import ElementTree
from sqlalchemy import select, delete,update,or_
from fastapi import HTTPException
from .database import Connector, OAuthState, Item, Inbox, now
from .config import settings
from .security import audit, expiry, expired

def cipher():
    if not settings.encryption_key: raise HTTPException(409,'Configura ENCRYPTION_KEY antes de conectar una cuenta')
    return Fernet(settings.encryption_key.encode())
def encode_tokens(tokens): return cipher().encrypt(json.dumps(tokens).encode()).decode()
def decode_tokens(value): return json.loads(cipher().decrypt(value.encode()))
def oauth_start(db,user,key):
    connector=db.get(Connector,(key,user.id))
    if key not in ['gmail','calendar','drive'] or not connector: raise HTTPException(404,'Conector no disponible')
    from .google_setup import credentials
    client_id,client_secret=credentials(db,user.id)
    if not client_id or not client_secret: raise HTTPException(409,'Configura la aplicación OAuth de Google en el servidor')
    cipher()
    state=secrets.token_urlsafe(32); verifier=secrets.token_urlsafe(64)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    db.add(OAuthState(id=state,user_id=user.id,connector=key,verifier=verifier,expires_at=expiry(10))); db.commit()
    params={'client_id':client_id,'redirect_uri':settings.public_origin+'/api/v1/oauth/google/callback','response_type':'code','scope':' '.join(connector.scopes),'access_type':'offline','prompt':'consent','state':state,'code_challenge':challenge,'code_challenge_method':'S256'}
    return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(params)}
def oauth_callback(db,state,code,error=None):
    row=db.get(OAuthState,state)
    if not row or expired(row.expires_at): raise HTTPException(400,'El permiso ha caducado. Vuelve a conectar Google.')
    connector_key,user_id,verifier=row.connector,row.user_id,row.verifier
    deleted=db.execute(delete(OAuthState).where(OAuthState.id==state))
    db.commit()
    if deleted.rowcount!=1: raise HTTPException(400,'Este permiso ya se ha utilizado')
    if error: return False
    from .google_setup import credentials
    client_id,client_secret=credentials(db,user_id)
    try:
        with httpx.Client(timeout=25) as client:
            response=client.post('https://oauth2.googleapis.com/token',data={'client_id':client_id,'client_secret':client_secret,'code':code,'code_verifier':verifier,'grant_type':'authorization_code','redirect_uri':settings.public_origin+'/api/v1/oauth/google/callback'})
            response.raise_for_status(); tokens=response.json()
        connector=db.get(Connector,(connector_key,user_id))
        granted=set(tokens.get('scope','').split())
        if not set(connector.scopes).issubset(granted): raise HTTPException(400,'Google no concedió los permisos necesarios')
        if not tokens.get('refresh_token'):
            old=decode_tokens(connector.encrypted_tokens) if connector.encrypted_tokens else {}
            tokens['refresh_token']=old.get('refresh_token')
        if not tokens.get('refresh_token'): raise HTTPException(400,'Google no concedió acceso de sincronización. Revoca y vuelve a conectar.')
        tokens['expires_at']=expiry(max(1,int(tokens.get('expires_in',3600))//60-1))
        connector.encrypted_tokens=encode_tokens(tokens); connector.status='connected'; connector.error=None;connector.revision+=1;connector.sync_started_at=None;connector.sync_details={};connector.last_sync=None
        db.commit(); return True
    except httpx.HTTPError:
        raise HTTPException(502,'Google no pudo completar la conexión. Inténtalo de nuevo.')

def access_token(db,connector):
    tokens=decode_tokens(connector.encrypted_tokens)
    from .google_setup import credentials
    client_id,client_secret=credentials(db,connector.user_id)
    if expired(tokens.get('expires_at','2000-01-01T00:00:00+00:00')):
        with httpx.Client(timeout=25) as client:
            response=client.post('https://oauth2.googleapis.com/token',data={'client_id':client_id,'client_secret':client_secret,'refresh_token':tokens.get('refresh_token'),'grant_type':'refresh_token'})
            response.raise_for_status(); update=response.json()
        tokens.update(update); tokens['expires_at']=expiry(max(1,int(tokens.get('expires_in',3600))//60-1))
        # Publish refreshed tokens only when this synchronization still owns its lease.
        db.info.setdefault('connector_token_updates',{})[(connector.id,connector.user_id)]=encode_tokens(tokens)
    return tokens['access_token']

def upsert(db,user,source,external_id,kind,area,title,description='',due_at=None,data=None,status='active'):
    if due_at: due_at=datetime.fromisoformat(due_at.replace('Z','+00:00')).astimezone(timezone.utc).isoformat(timespec='microseconds')
    data={k:v for k,v in (data or {}).items() if k not in ['url','tracking_url'] or (isinstance(v,str) and v.startswith(('http://','https://')))}
    item=db.scalar(select(Item).where(Item.user_id==user.id,Item.source==source,Item.external_id==external_id))
    created=item is None
    if created:
        item=Item(user_id=user.id,source=source,external_id=external_id,kind=kind,area=area,title=title[:240],description=description[:150000],due_at=due_at,data=data or {},status=status)
        db.add(item); db.flush()
    else:
        # A successful read of unchanged upstream content is not a new resource
        # version. Keep source references valid and avoid waking every review.
        effective={'title':title[:240],'description':description[:150000],'data':data or {},'due_at':due_at,'status':status}
        if all(getattr(item,key)==value for key,value in effective.items()):return False
        item.title=title[:240]; item.description=description[:150000]; item.data=data or {}; item.due_at=due_at; item.status=status; item.updated_at=now(); item.version+=1
    if created and kind in ['email','document']:
        db.add(Inbox(user_id=user.id,item_id=item.id,title=item.title,area=area,reason='Nuevo recurso sincronizado.'))
    return created

def extract_order(text):
    low=text.lower()
    shop='Amazon' if 'amazon' in low else 'AliExpress' if 'aliexpress' in low else None
    if not shop or not any(x in low for x in ['pedido','order','enviado','shipped','entregado']): return None
    status='delivered' if any(x in low for x in ['entregado','delivered']) else 'in_transit'
    return shop,status

def google_sync(db,user,connector):
    token=access_token(db,connector); count=0; pages=0; truncated=False
    window=(connector.sync_details or {}).get('gmail_window') if connector.id=='gmail' else None
    if connector.id=='gmail' and not window:
        until=int(datetime.now(timezone.utc).timestamp())
        window={'until':until,'after':int(datetime.fromisoformat(connector.last_sync).timestamp())-60 if connector.last_sync else None,'cursor':None}
    with httpx.Client(timeout=25,headers={'Authorization':'Bearer '+token}) as client:
        next_token=window.get('cursor') if window else None
        for page in range(5):
            pages+=1
            params={}
            if next_token: params['pageToken']=next_token
            if connector.id=='gmail':
                params['maxResults']=100
                params['q']='before:'+str(window['until'])
                if window.get('after') is not None:params['q']+=' after:'+str(window['after'])
                r=client.get('https://gmail.googleapis.com/gmail/v1/users/me/messages',params=params); r.raise_for_status(); listing=r.json()
                for record in listing.get('messages',[]):
                    m=client.get('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+record['id'],params={'format':'full'}); m.raise_for_status(); email=m.json()
                    headers={h['name'].lower():h['value'] for h in email.get('payload',{}).get('headers',[])}
                    title=headers.get('subject','Sin asunto'); text=email.get('snippet','')
                    area='personal'
                    count+=upsert(db,user,'gmail',record['id'],'email',area,title,text,data={'sender':headers.get('from',''),'url':'https://mail.google.com/mail/u/0/#all/'+record['id']})
                    order=extract_order(title+' '+text)
                    if order: count+=upsert(db,user,'gmail-order',record['id'],'order','personal',title,text,status=order[1],data={'store':order[0],'url':'https://mail.google.com/mail/u/0/#all/'+record['id']})
            elif connector.id=='calendar':
                params.update({'maxResults':250,'singleEvents':'true','orderBy':'startTime','timeMin':now(),'timeMax':(datetime.now(timezone.utc)+timedelta(days=90)).isoformat()})
                r=client.get('https://www.googleapis.com/calendar/v3/calendars/primary/events',params=params); r.raise_for_status(); listing=r.json()
                for event in listing.get('items',[]):
                    start=event.get('start',{}); end=event.get('end',{})
                    due=start.get('dateTime') or (start.get('date','')+'T00:00:00+00:00' if start.get('date') else None)
                    count+=upsert(db,user,'calendar',event['id'],'event','personal',event.get('summary','Sin título'),event.get('description',''),due_at=due,status='cancelled' if event.get('status')=='cancelled' else 'active',data={'location':event.get('location',''),'end_at':end.get('dateTime') or end.get('date'),'url':event.get('htmlLink',''),'all_day':'date' in start,'start_date':start.get('date')})
            else:
                params.update({'pageSize':100,'fields':'nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,description)','q':'trashed = false','orderBy':'modifiedTime desc'})
                r=client.get('https://www.googleapis.com/drive/v3/files',params=params); r.raise_for_status(); listing=r.json()
                for file in listing.get('files',[]):
                    count+=upsert(db,user,'drive',file['id'],'document','personal',file['name'],file.get('description',''),data={'url':file.get('webViewLink','https://drive.google.com/file/d/'+file['id']+'/view'),'mime':file['mimeType'],'modified_at':file.get('modifiedTime','')})
            next_token=listing.get('nextPageToken')
            if not next_token: break
        truncated=bool(next_token)
    result={'created':count,'pages':pages,'partial':truncated,'note':'Google Drive sincroniza metadatos; importa un PDF para indexar su contenido.' if connector.id=='drive' else 'Calendario: próximos 90 días. Gmail: nuevos mensajes desde la última sincronización.'}
    if window:
        result['gmail_window']={**window,'cursor':next_token} if truncated else None
        result['watermark']=datetime.fromtimestamp(window['until'],timezone.utc).isoformat()
    return result

def validate_public_url(url):
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in [None,443]: raise ValueError('RSS requiere una URL HTTPS pública')
    addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise ValueError('No se permiten redes privadas en RSS')

def rss_sync(db,user):
    count=0
    urls=[u.strip() for u in settings.rss_urls.split(',') if u.strip()]
    if not urls: raise HTTPException(409,'Configura RSS_URLS en el servidor')
    for url in urls:
        validate_public_url(url)
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            with client.stream('GET',url) as r:
                r.raise_for_status(); chunks=[]; size=0
                for chunk in r.iter_bytes():
                    size+=len(chunk)
                    if size>2_000_000: raise ValueError('El feed supera el límite de tamaño')
                    chunks.append(chunk)
        tree=ElementTree.fromstring(b''.join(chunks)); ns={'a':'http://www.w3.org/2005/Atom'}
        entries=tree.findall('.//item') or tree.findall('a:entry',ns)
        for entry in entries[:50]:
            title=entry.findtext('title') or entry.findtext('a:title',namespaces=ns) or 'Sin título'
            link=entry.findtext('link')
            if not link:
                element=entry.find('a:link',ns); link=element.get('href') if element is not None else ''
            description=entry.findtext('description') or entry.findtext('a:summary',namespaces=ns) or ''
            description=re.sub('<[^>]+>',' ',description)
            external=entry.findtext('guid') or entry.findtext('a:id',namespaces=ns) or link or title
            data={'url':link} if link and link.startswith(('https://','http://')) else {}
            count+=upsert(db,user,'rss',hashlib.sha256((url+external).encode()).hexdigest(),'news','technology',title,description,data=data)
    return {'created':count,'feeds':len(urls)}

def recover_connector_leases(db):
    cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
    recovered=db.execute(update(Connector).where(Connector.status=='syncing',or_(Connector.sync_started_at.is_(None),Connector.sync_started_at<cutoff)).values(status='error',error='La sincronización se interrumpió. Puedes volver a sincronizar.',sync_started_at=None,revision=Connector.revision+1))
    db.commit()
    return recovered.rowcount

def sync_connector(db,user,key):
    connector=db.get(Connector,(key,user.id))
    if not connector: raise HTTPException(404,'Conector no encontrado')
    if connector.status=='paused': raise HTTPException(409,'Reanuda el conector antes de sincronizar')
    if connector.status=='syncing':raise HTTPException(409,'Este conector ya se está sincronizando')
    if key in ['gmail','calendar','drive','github','notion','vercel'] and not connector.encrypted_tokens: raise HTTPException(409,'Autoriza este conector antes de sincronizar')
    if key not in ['gmail','calendar','drive','rss','server','github','notion','vercel','weather']: raise HTTPException(409,'Esta integración está prevista y aún no está implementada')
    original_revision=connector.revision
    claimed=db.execute(update(Connector).where(Connector.id==key,Connector.user_id==user.id,Connector.revision==original_revision,Connector.status==connector.status).values(status='syncing',sync_started_at=now(),revision=Connector.revision+1))
    db.commit()
    if claimed.rowcount!=1:raise HTTPException(409,'La conexión ha cambiado. Vuelve a intentarlo')
    db.refresh(connector);revision=connector.revision
    token_key=(key,user.id)
    db.info.setdefault('connector_token_updates',{}).pop(token_key,None)
    try:
        if key in ['gmail','calendar','drive']: result=google_sync(db,user,connector)
        elif key=='rss': result=rss_sync(db,user)
        elif key=='weather':
            from .weather import refresh
            result=refresh(db,user,commit=False)
        elif key in ['github','notion','vercel']:
            from .account_connectors import account_sync
            result=account_sync(db,user,connector)
        else:
            from .server import server_health
            result=server_health()
        values={'status':'connected','last_sync':now(),'error':None,'sync_started_at':None}
        if key=='weather':values['sync_details']={'location':connector.sync_details.get('location'),'forecast':result}
        else:values['sync_details']=result
        if key=='gmail':values['last_sync']=connector.last_sync if result.get('partial') else result['watermark']
        refreshed_token=db.info['connector_token_updates'].pop(token_key,None)
        if refreshed_token:values['encrypted_tokens']=refreshed_token
        finished=db.execute(update(Connector).where(Connector.id==key,Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(**values))
        if finished.rowcount!=1:raise HTTPException(409,'La conexión cambió durante la sincronización. Se descartó la importación')
        audit(db,user,'connector.synced',key); db.commit(); return result
    except Exception as exc:
        db.rollback();db.info['connector_token_updates'].pop(token_key,None)
        message=exc.detail if isinstance(exc,HTTPException) else 'La sincronización falló. Comprueba la conexión y los permisos.'
        db.execute(update(Connector).where(Connector.id==key,Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status='error',error=message,sync_started_at=None))
        db.commit()
        if isinstance(exc,HTTPException): raise
        raise HTTPException(502,'La sincronización falló. Comprueba la conexión y los permisos.')
