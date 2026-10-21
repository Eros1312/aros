"""Read-only, authorized Moodle import for Aula Virtual UV; no password or SSO scraping."""
import hashlib
import html
import io
import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from pydantic import BaseModel, SecretStr
from pypdf import PdfReader
from sqlalchemy import select, func

from .connectors import encode_tokens, decode_tokens
from .database import Automation, Connector, Inbox, Item, get_db, now
from .security import audit, current_user

BASE_URL = 'https://aulavirtual.uv.es'
HOST = 'aulavirtual.uv.es'
MAX_COURSES = 100
MAX_FILES = 200
MAX_FILE_BYTES = 10_000_000
MAX_BROWSER_BYTES = 30_000_000
MAX_TOTAL_BYTES = 100_000_000
MAX_SECONDS = 600
FUNCTIONS = {'core_webservice_get_site_info', 'core_enrol_get_users_courses', 'core_course_get_contents'}
TYPES = {'.pdf':'application/pdf', '.txt':'text/plain', '.md':'text/markdown', '.csv':'text/csv',
         '.doc':'application/msword', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.ppt':'application/vnd.ms-powerpoint', '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
         '.xls':'application/vnd.ms-excel', '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         '.odt':'application/vnd.oasis.opendocument.text', '.ods':'application/vnd.oasis.opendocument.spreadsheet',
         '.odp':'application/vnd.oasis.opendocument.presentation', '.rtf':'application/rtf',
         '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.zip':'application/zip'}
router = APIRouter(prefix='/api/v1/connectors/university', tags=['Aula Virtual UV'])
UserDep = Annotated[object, Depends(current_user)]
DbDep = Annotated[object, Depends(get_db)]

class UniversityCredentialIn(BaseModel):
    token: SecretStr

def public_target(url):
    """Pin a public DNS result while retaining the university hostname for TLS and Host."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != HOST or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError('origin')
        addresses = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(entry[4][0] for entry in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError('private')
        return str(httpx.URL(url).copy_with(host=ips[0])), {'Host': HOST}, {'sni_hostname': HOST}
    except (ValueError, OSError, httpx.InvalidURL):
        raise HTTPException(422, 'Solo se permite el origen HTTPS público de Aula Virtual UV.') from None

def read_remote(client, method, url, limit, data=None):
    target, headers, extensions = public_target(url)
    try:
        with client.stream(method, target, headers=headers, extensions=extensions, data=data) as response:
            if response.status_code in (401, 403):
                raise HTTPException(409, 'Aula Virtual rechazó el token o los permisos. Solicita acceso autorizado a su API.')
            if 300 <= response.status_code < 400:
                raise HTTPException(409, 'Aula Virtual requiere otra autenticación o redirección. No se ha iniciado una sesión SSO.')
            if response.status_code == 429:
                raise HTTPException(429, 'Aula Virtual ha limitado las peticiones. Reintenta más tarde.')
            response.raise_for_status()
            declared = response.headers.get('content-length')
            if declared and int(declared) > limit:
                raise HTTPException(413, 'El archivo o respuesta supera el límite de importación.')
            chunks = []; size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, 'El archivo o respuesta supera el límite de importación.')
                chunks.append(chunk)
            return b''.join(chunks), response.headers.get('content-type', '').split(';')[0].lower()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        # Request URLs for Moodle downloads contain a secret: never propagate their exceptions.
        raise HTTPException(502, 'No se pudo consultar Aula Virtual. Revisa el servicio y los permisos de la API.') from None

def moodle_call(client, token, function, **params):
    if function not in FUNCTIONS:
        raise HTTPException(422, 'Función Moodle no permitida')
    body = {'wstoken': token, 'wsfunction': function, 'moodlewsrestformat': 'json', **params}
    raw, _ = read_remote(client, 'POST', BASE_URL+'/webservice/rest/server.php', 5_000_000, body)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(409, 'La API Moodle no está disponible con este acceso. Solicita al SIUV un token de lectura autorizado.') from None
    if isinstance(result, dict) and ('exception' in result or 'errorcode' in result):
        raise HTTPException(409, 'Moodle no autorizó esta consulta. El SIUV debe habilitar el servicio y sus permisos de lectura y descarga.')
    return result

def site_info(client, token):
    info = moodle_call(client, token, 'core_webservice_get_site_info')
    if not isinstance(info, dict) or not isinstance(info.get('userid'), int) or info['userid'] < 1:
        raise HTTPException(409, 'Moodle no devolvió una identidad de alumno válida.')
    if info.get('siteurl', '').rstrip('/') != BASE_URL:
        raise HTTPException(409, 'El token no corresponde al Aula Virtual UV configurado.')
    functions = {f.get('name') for f in info.get('functions', []) if isinstance(f, dict)}
    if not FUNCTIONS.issubset(functions) or info.get('downloadfiles') not in (1, True):
        raise HTTPException(409, 'El servicio Moodle necesita consultar identidad, asignaturas y contenidos, y permitir descargar archivos. Solicita esos permisos al SIUV.')
    return info

def client_for_university():
    return httpx.Client(timeout=httpx.Timeout(30, connect=8), follow_redirects=False, trust_env=False,
                        limits=httpx.Limits(max_keepalive_connections=0))

def configure_university(db, user, token):
    token = token.strip()
    if not 10 <= len(token) <= 4096 or any(c.isspace() for c in token):
        raise HTTPException(422, 'Introduce el token autorizado en el panel de Aros; no una contraseña.')
    # Fail before making an external call if encryption has not been configured.
    encrypted = encode_tokens({'access_token': token, 'base_url': BASE_URL})
    with client_for_university() as client:
        info = site_info(client, token)
    connector = db.get(Connector, ('university', user.id))
    if not connector:
        connector = Connector(id='university', user_id=user.id, name='Aula Virtual UV')
        db.add(connector)
    connector.encrypted_tokens = encrypted
    connector.status = 'connected'; connector.error = None; connector.revision = (connector.revision or 1) + 1; connector.sync_started_at = None
    connector.last_sync = None
    connector.scopes = sorted(FUNCTIONS) + ['downloadfiles']
    connector.sync_details = {'site_url': BASE_URL, 'account': str(info.get('fullname') or '')[:200],
                              'configured': True, 'note': 'Acceso validado; primera importación pendiente.'}
    automation = db.scalar(select(Automation).where(Automation.user_id == user.id, Automation.kind == 'university_sync'))
    if not automation:
        automation = Automation(user_id=user.id, title='Sincronizar Aula Virtual UV', kind='university_sync')
        db.add(automation)
    automation.enabled = True; automation.interval_minutes = 360; automation.next_run = now()
    audit(db, user, 'connector.configured', 'university'); db.commit()
    return {'configured': True, 'requires_sync': True, 'site_url': BASE_URL, 'interval_minutes': 360}

def clean_text(value):
    return html.unescape(re.sub('<[^>]+>', ' ', str(value or '')))[:150000]

def download_url(value, token):
    if not isinstance(value, str): raise HTTPException(422, 'URL de archivo inválida')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname != HOST or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise HTTPException(422, 'El archivo se aloja fuera de Aula Virtual UV.')
    decoded_path = unquote(parsed.path)
    if (not decoded_path.startswith('/webservice/pluginfile.php/') or '\\' in decoded_path
            or any(part in ('.', '..') for part in decoded_path.split('/'))):
        raise HTTPException(422, 'El archivo no utiliza el endpoint autorizado de descarga Moodle.')
    # Retain Moodle's non-secret download options, replacing any pre-existing token.
    params = [(key, value) for key, value in parse_qsl(parsed.query) if key in ('forcedownload', 'preview')]
    return urlunsplit(('https', HOST, parsed.path, urlencode(params + [('token', token)]), ''))

def extract_document(content, extension):
    text = ''; warning = None; partial = False
    if extension in ('.txt', '.md', '.csv'):
        try:
            text = content.decode('utf-8-sig')[:150000]; partial = len(content) > 150000
        except UnicodeDecodeError:
            warning = 'Texto descargado; codificación distinta de UTF-8, sin indexar.'
    elif extension == '.pdf':
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted: raise ValueError('encrypted')
            text = '\n'.join((page.extract_text() or '')[:10000] for page in reader.pages[:40])[:150000]
            partial = True
            if not text.strip(): warning = 'PDF descargado sin texto extraíble; puede requerir OCR.'
        except Exception:
            warning = 'PDF descargado sin indexar; no se pudo extraer el texto o está cifrado.'
    elif extension in ('.docx', '.pptx', '.xlsx', '.odt', '.ods', '.odp'):
        from zipfile import ZipFile
        from defusedxml import ElementTree
        try:
            with ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 20_000_000:
                    raise ValueError('expanded_size')
                names = archive.namelist()
                if extension == '.docx':
                    selected = ['word/document.xml']
                elif extension == '.pptx':
                    selected = sorted((name for name in names if re.fullmatch(r'ppt/slides/slide[0-9]+\.xml', name)),
                                      key=lambda name: int(re.search(r'slide([0-9]+)', name).group(1)))
                elif extension == '.xlsx':
                    selected = sorted(name for name in names if re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', name))
                else:
                    selected = ['content.xml']
                if not selected: raise ValueError('missing_content')
                partial = len(selected) > 80
                def read_xml(name):
                    if archive.getinfo(name).file_size > 2_000_000: raise ValueError('xml_size')
                    return ElementTree.fromstring(archive.read(name))
                shared = []
                if extension == '.xlsx' and 'xl/sharedStrings.xml' in names:
                    shared = [''.join(node.itertext()) for node in read_xml('xl/sharedStrings.xml')]
                parts = []; length = 0
                for name in selected[:80]:
                    root = read_xml(name)
                    if extension == '.xlsx':
                        values = []
                        for cell in root.iter():
                            if cell.tag.rsplit('}', 1)[-1] != 'c': continue
                            value = next((node.text or '' for node in cell if node.tag.rsplit('}', 1)[-1] == 'v'), '')
                            if cell.get('t') == 's':
                                value = shared[int(value)] if value.isdigit() and int(value) < len(shared) else ''
                            elif cell.get('t') == 'inlineStr':
                                value = ''.join(node.text or '' for node in cell.iter() if node.tag.rsplit('}', 1)[-1] == 't')
                            if value: values.append(value)
                        chunk = '\t'.join(values)
                    elif extension in ('.docx', '.pptx'):
                        chunk = ' '.join(node.text or '' for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 't')
                    else:
                        chunk = ' '.join(root.itertext())
                    parts.append(chunk); length += len(chunk)
                    if length >= 150000:
                        partial = True; break
                text = '\n'.join(parts)[:150000]
                if not text.strip(): warning = 'Archivo descargado sin texto extraíble.'
        except Exception:
            warning = 'Archivo descargado sin indexar; formato Office inválido, cifrado o superior al límite seguro de extracción.'
            text = ''; partial = True
    else:
        warning = 'Archivo descargado; este formato no tiene extracción de texto en Aros.'
    text=text.replace(chr(0),'')
    return text, warning, partial

def save_course(db, user, course):
    key = 'course:'+str(course['id'])
    item = db.scalar(select(Item).where(Item.user_id == user.id, Item.source == 'university', Item.external_id == key))
    title = clean_text(course.get('fullname') or course.get('shortname') or 'Asignatura')[:240]
    description = clean_text(course.get('summary'))
    data = {'course_id': course['id'], 'url': BASE_URL+'/course/view.php?id='+str(course['id'])}
    if not item:
        db.add(Item(user_id=user.id, source='university', external_id=key, kind='project', area='university',
                    title=title, description=description, data=data, status='active'))
        return True
    if (item.title, item.description, item.data) != (title, description, data):
        item.title = title; item.description = description; item.data = data; item.version += 1; item.updated_at = now()
    return False

def university_sync(db, user, connector):
    if not connector.encrypted_tokens:
        raise HTTPException(409, 'Autoriza Aula Virtual con un token de lectura y descarga antes de sincronizar.')
    tokens = decode_tokens(connector.encrypted_tokens)
    if tokens.get('base_url') != BASE_URL:
        raise HTTPException(409, 'Vuelve a autorizar el Aula Virtual UV configurado.')
    token = tokens['access_token']
    created = updated = unchanged = skipped = total_bytes = downloaded = course_count = 0
    partial = False; deadline = time.monotonic()+MAX_SECONDS; seen = set(); issues = []
    with client_for_university() as client:
        info = site_info(client, token)
        courses = moodle_call(client, token, 'core_enrol_get_users_courses', userid=info['userid'])
        if not isinstance(courses, list): raise HTTPException(502, 'Moodle no devolvió una lista válida de asignaturas.')
        partial = len(courses) > MAX_COURSES
        for course in courses[:MAX_COURSES]:
            if not isinstance(course, dict) or not isinstance(course.get('id'), int): continue
            if time.monotonic() > deadline: partial = True; break
            course_count += 1; created += save_course(db, user, course)
            sections = moodle_call(client, token, 'core_course_get_contents', courseid=course['id'])
            if not isinstance(sections, list): raise HTTPException(502, 'Moodle no devolvió contenidos válidos de la asignatura.')
            for section in sections:
                for module in section.get('modules', []):
                    for file in module.get('contents', []):
                        if not isinstance(file, dict) or file.get('type') != 'file': continue
                        name = Path(str(file.get('filename') or 'Archivo').replace('\\', '/')).name[:200]
                        extension = Path(name).suffix.lower()
                        if extension not in TYPES: skipped += 1; continue
                        identity = json.dumps([course['id'], module.get('id'), file.get('filepath'), file.get('filename')], ensure_ascii=False)
                        key = 'file:'+hashlib.sha256(identity.encode()).hexdigest()
                        if key in seen: continue
                        seen.add(key)
                        fingerprint = hashlib.sha256(json.dumps([file.get('timemodified'), file.get('filesize')]).encode()).hexdigest()
                        item = db.scalar(select(Item).where(Item.user_id == user.id, Item.source == 'university', Item.external_id == key))
                        reliable_metadata = isinstance(file.get('timemodified'), int) and isinstance(file.get('filesize'), int)
                        if reliable_metadata and item and item.blob is not None and item.data.get('remote_fingerprint') == fingerprint:
                            unchanged += 1; continue
                        if downloaded >= MAX_FILES or total_bytes >= MAX_TOTAL_BYTES or time.monotonic() > deadline:
                            partial = True; continue
                        try:
                            size = file.get('filesize', 0)
                            if not isinstance(size, int) or size < 0: raise HTTPException(422, 'Tamaño inválido')
                            if size > MAX_FILE_BYTES: raise HTTPException(413, 'Archivo mayor de 10 MB')
                            if size > MAX_TOTAL_BYTES-total_bytes: partial = True; continue
                            url = download_url(file.get('fileurl'), token)
                            content, mime = read_remote(client, 'GET', url, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES-total_bytes))
                            total_bytes += len(content); downloaded += 1
                            if mime in ('text/html', 'application/json') or not content or (extension == '.pdf' and not content.startswith(b'%PDF-')):
                                raise HTTPException(422, 'La respuesta no contiene el documento esperado')
                        except HTTPException as exc:
                            if exc.status_code in (409, 429, 502): raise
                            skipped += 1
                            if len(issues) < 10: issues.append({'filename': name, 'reason': str(exc.detail)[:200]})
                            continue
                        text, warning, extracted_partial = extract_document(content, extension)
                        metadata = {'filename': name, 'mime': TYPES[extension], 'size': len(content), 'extracted': bool(text),
                                    'partial': extracted_partial, 'course_id': course['id'],
                                    'course_title': clean_text(course.get('fullname'))[:240], 'module_id': module.get('id'),
                                    'remote_fingerprint': fingerprint, 'sha256': hashlib.sha256(content).hexdigest(),
                                    'url': BASE_URL+'/course/view.php?id='+str(course['id'])}
                        if warning: metadata['extraction_note'] = warning
                        if not item:
                            item = Item(user_id=user.id, source='university', external_id=key, kind='document', area='university',
                                        title=name, description=text, data=metadata, blob=content, status='active')
                            db.add(item); db.flush(); created += 1
                            db.add(Inbox(user_id=user.id, item_id=item.id, title=name, area='university', reason='Documento de Aula Virtual descargado.'))
                        else:
                            if (item.title, item.description, item.data, item.blob) == (name, text, metadata, content):
                                unchanged += 1
                            else:
                                item.title = name; item.description = text; item.data = metadata; item.blob = content
                                item.updated_at = now(); item.version += 1; updated += 1
    return {'created': created, 'updated': updated, 'unchanged': unchanged, 'downloaded': downloaded, 'courses': course_count,
            'skipped': skipped, 'bytes': total_bytes, 'partial': partial, 'issues': issues,
            'note': 'Se importan las asignaturas matriculadas accesibles y sus archivos autorizados. PDF: hasta 40 páginas, sin OCR. DOCX/PPTX/XLSX y OpenDocument: texto acotado. Office antiguo e imágenes: descarga sin extracción. Archivos borrados en Moodle se conservan en Aros. Los pendientes por límite se intentan en la siguiente sincronización.'}

@router.get('/setup')
def setup(user: UserDep, db: DbDep):
    connector = db.get(Connector, ('university', user.id))
    imported=db.scalar(select(func.count()).select_from(Item).where(Item.user_id==user.id,Item.kind=='document',Item.source.in_(['university','university-browser'])))
    return {'imported_documents': imported, 'site_url': BASE_URL, 'configured': bool(connector and connector.encrypted_tokens),
            'status': connector.status if connector else 'disconnected', 'interval_minutes': 360,
            'requires': 'Token Moodle autorizado con lectura de identidad, asignaturas y contenidos y descarga de archivos. Si no aparece en Preferencias → Claves de seguridad, solicítalo al SIUV; su disponibilidad para alumnos no está verificada.'}

@router.post('/setup')
def configure(payload: UniversityCredentialIn, user: UserDep, db: DbDep):
    return configure_university(db, user, payload.token.get_secret_value())

@router.post('/sync')
def sync(user: UserDep, db: DbDep):
    from .connectors import sync_connector
    return sync_connector(db, user, 'university')


@router.post('/import', status_code=201)
def import_browser_document(user: UserDep, db: DbDep, file: UploadFile=File(...),
                            course_title: str=Form(...), course_url: str=Form(...), resource_url: str=Form(...)):
    """Import an authorized browser download without persisting university sessions."""
    course = urlsplit(course_url); resource = urlsplit(resource_url)
    for parsed in (course, resource):
        if parsed.scheme != 'https' or parsed.hostname != HOST or parsed.username or parsed.password or parsed.port not in (None,443):
            raise HTTPException(422, 'El documento debe proceder de Aula Virtual UV.')
    if course.path != '/course/view.php' or not dict(parse_qsl(course.query)).get('id','').isdigit():
        raise HTTPException(422, 'Asignatura de Aula Virtual inválida.')
    if resource.path not in ('/mod/resource/view.php','/mod/folder/view.php') and not resource.path.startswith('/pluginfile.php/'):
        raise HTTPException(422, 'Recurso de Aula Virtual inválido.')
    # Never store query tokens or browser session identifiers supplied in an import.
    safe_query = [(k,v) for k,v in parse_qsl(resource.query) if k=='id' and v.isdigit()]
    resource_url = urlunsplit(('https',HOST,resource.path,urlencode(safe_query),''))
    course_url = BASE_URL+'/course/view.php?id='+dict(parse_qsl(course.query))['id']
    name = Path(file.filename or 'Documento').name[:200]; extension=Path(name).suffix.lower()
    if extension not in TYPES: raise HTTPException(422, 'Formato de documento no admitido.')
    content=file.file.read(MAX_BROWSER_BYTES+1)
    if len(content)>MAX_BROWSER_BYTES: raise HTTPException(413, 'El archivo supera 30 MB para importación de sesión autorizada.')
    if not content or (extension=='.pdf' and not content.startswith(b'%PDF-')):
        raise HTTPException(422, 'No se recibió el documento esperado.')
    save_course(db,user,{'id':int(dict(parse_qsl(course.query))['id']),'fullname':course_title[:240],'shortname':course_title[:240]})
    text,warning,partial=extract_document(content,extension)
    digest=hashlib.sha256(content).hexdigest()
    key=hashlib.sha256((course_url+'|'+resource_url+'|'+name).encode()).hexdigest()
    item=db.scalar(select(Item).where(Item.user_id==user.id,Item.source=='university-browser',Item.external_id==key))
    if item and item.data.get('sha256')==digest:return {'id':item.id,'created':False,'unchanged':True}
    data={'filename':name,'mime':TYPES[extension],'size':len(content),'sha256':digest,'extracted':bool(text),'partial':partial,
          'course_title':course_title[:240],'course_url':course_url,'url':resource_url,'import_method':'authorized_browser',
          'note':'Descarga de la sesión autorizada. La actualización automática requiere permiso API de la UV.'}
    if warning:data['extraction_note']=warning
    created=item is None
    if created:
        item=Item(user_id=user.id,source='university-browser',external_id=key,kind='document',area='university',
                  title=name,description=text,data=data,blob=content,status='active')
        db.add(item);db.flush()
        db.add(Inbox(user_id=user.id,item_id=item.id,title=name,area='university',reason='Documento de Aula Virtual importado.'))
    else:
        item.description=text;item.data=data;item.blob=content;item.version+=1;item.updated_at=now()
    audit(db,user,'university.document_imported',item.id);db.commit()
    return {'id':item.id,'created':created,'unchanged':False,'extracted':bool(text)}
