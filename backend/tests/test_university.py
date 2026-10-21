"""Offline Moodle fixtures: transport, authorization, owner isolation and import continuity."""
import json
import socket
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import university as uv
from app.config import settings
from app.connectors import decode_tokens
from app.database import Base, User, Connector, Item, Automation

TOKEN = 'authorized-fixture-token'

@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, 'encryption_key', Fernet.generate_key().decode())
    with Session(engine) as session:
        yield session
    engine.dispose()

@pytest.fixture
def user(db):
    user = User(email='student@fixture', password_hash='fixture')
    db.add(user); db.flush()
    return user

@pytest.fixture
def moodle(monkeypatch):
    state = {'calls': [], 'downloads': [], 'files': [
        {'type':'file', 'filename':'Apuntes.txt', 'filepath':'/', 'filesize':6, 'timemodified':1,
         'fileurl':uv.BASE_URL+'/webservice/pluginfile.php/11/mod_resource/content/0/Apuntes.txt?token=old&forcedownload=1'}],
        'content':b'Apunte', 'course_count':1}
    def handle(request):
        state['calls'].append(request)
        assert request.url.host == '93.184.216.34'
        assert request.headers['host'] == uv.HOST
        assert request.extensions['sni_hostname'] == uv.HOST
        if request.method == 'POST':
            assert 'token' not in str(request.url)
            body = parse_qs(request.content.decode())
            assert body['wstoken'] == [TOKEN]
            function = body['wsfunction'][0]
            if state.get('api_error'): return httpx.Response(200, json={'exception':'invalid_token', 'message':TOKEN})
            if function == 'core_webservice_get_site_info':
                result = {'userid':7, 'siteurl':uv.BASE_URL, 'fullname':'Alumno', 'downloadfiles':1,
                          'functions':[{'name':f} for f in uv.FUNCTIONS]}
                result.update(state.get('site_overrides', {}))
            elif function == 'core_enrol_get_users_courses':
                assert body['userid'] == ['7']
                result = [{'id':n+1, 'fullname':f'Asignatura {n+1}', 'summary':'<p>Contenido</p>'}
                          for n in range(state['course_count'])]
            else:
                result = [{'modules':[{'id':9, 'contents':state['files']}]}]
            return httpx.Response(200, json=result)
        state['downloads'].append(request)
        assert request.url.params['token'] == TOKEN
        return httpx.Response(state.get('download_status',200), content=state['content'],
                              headers={'content-type':state.get('mime','text/plain')})
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(uv, 'client_for_university', lambda: httpx.Client(transport=transport, follow_redirects=False))
    monkeypatch.setattr(uv.socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    return state

def connected(db, user):
    uv.configure_university(db, user, TOKEN)
    return db.get(Connector, ('university', user.id))

def test_configuration_encrypts_token_and_enables_owner_automation(db,user,moodle):
    result = uv.configure_university(db,user,TOKEN)
    connector = db.get(Connector,('university',user.id))
    assert result['configured'] and result['interval_minutes'] == 360
    assert TOKEN not in connector.encrypted_tokens and TOKEN not in json.dumps(result)
    assert decode_tokens(connector.encrypted_tokens)['access_token'] == TOKEN
    automation = db.scalar(select(Automation))
    assert automation.user_id == user.id and automation.kind == 'university_sync'
    assert automation.enabled and automation.interval_minutes == 360
    uv.configure_university(db,user,TOKEN)
    assert len(list(db.scalars(select(Automation)))) == 1

@pytest.mark.parametrize('override', [{'userid':0}, {'siteurl':'https://another.example'}, {'downloadfiles':0}, {'functions':[]}])
def test_configuration_rejects_wrong_identity_origin_or_permissions(db,user,moodle,override):
    moodle['site_overrides'] = override
    with pytest.raises(HTTPException) as error: uv.configure_university(db,user,TOKEN)
    assert error.value.status_code == 409
    assert not db.get(Connector,('university',user.id))
    assert not db.scalar(select(Automation))

def test_missing_encryption_prevents_any_external_request(db,user,moodle,monkeypatch):
    monkeypatch.setattr(settings,'encryption_key','')
    with pytest.raises(HTTPException): uv.configure_university(db,user,TOKEN)
    assert moodle['calls'] == []

def test_import_downloads_indexes_and_reuses_items_without_mutation(db,user,moodle):
    connector = connected(db,user)
    first = uv.university_sync(db,user,connector); db.commit()
    assert first['courses'] == 1 and first['created'] == 2 and first['downloaded'] == 1
    document = db.scalar(select(Item).where(Item.kind=='document'))
    assert document.blob == b'Apunte' and document.description == 'Apunte'
    assert document.area == 'university' and document.source == 'university'
    assert TOKEN not in json.dumps(document.data) and 'old' not in json.dumps(document.data)
    version = document.version
    second = uv.university_sync(db,user,connector); db.commit()
    assert second['created'] == 0 and second['updated'] == 0 and second['unchanged'] == 1
    assert len(moodle['downloads']) == 1 and document.version == version
    assert len(list(db.scalars(select(Item)))) == 2
    moodle['files'][0]['timemodified'] = 2; moodle['content'] = b'Nuevo!'
    changed = uv.university_sync(db,user,connector); db.commit()
    assert changed['updated'] == 1 and document.blob == b'Nuevo!' and document.version == version+1


def test_import_is_owner_scoped_and_downloads_without_metadata_do_not_increment_versions(db,user,moodle):
    connector = connected(db,user)
    moodle['files'][0].pop('timemodified')
    uv.university_sync(db,user,connector); db.commit()
    own = db.scalar(select(Item).where(Item.kind=='document'))
    other = User(email='other@fixture',password_hash='fixture'); db.add(other); db.flush()
    private = Item(user_id=other.id,source='university',external_id=own.external_id,kind='document',area='university',title='Private',blob=b'private')
    db.add(private); db.commit()
    version = own.version
    result = uv.university_sync(db,user,connector); db.commit()
    assert result['unchanged'] == 1 and own.version == version
    assert private.blob == b'private' and private.title == 'Private'
    assert len(list(db.scalars(select(Item).where(Item.user_id==user.id)))) == 2


def test_file_limit_continues_remaining_files_next_sync(db,user,moodle,monkeypatch):
    connector = connected(db,user)
    moodle['files'].append({**moodle['files'][0], 'filename':'Segundo.txt',
                            'fileurl':uv.BASE_URL+'/webservice/pluginfile.php/12/mod_resource/content/0/Segundo.txt'})
    monkeypatch.setattr(uv,'MAX_FILES',1)
    first = uv.university_sync(db,user,connector); db.commit()
    assert first['partial'] and first['downloaded'] == 1
    second = uv.university_sync(db,user,connector); db.commit()
    assert second['downloaded'] == 1 and second['unchanged'] == 1 and not second['partial']
    assert len(list(db.scalars(select(Item).where(Item.kind=='document')))) == 2

@pytest.mark.parametrize('file_patch', [
    {'filesize':10_000_001}, {'filename':'Unsafe.exe'},
    {'fileurl':'https://evil.example/webservice/pluginfile.php/1/x.txt'},
    {'fileurl':uv.BASE_URL+'/webservice/pluginfile.php/%2e%2e/login.php'}])
def test_unsupported_large_external_or_traversal_files_are_not_downloaded(db,user,moodle,file_patch):
    connector = connected(db,user); moodle['files'][0].update(file_patch)
    result = uv.university_sync(db,user,connector); db.commit()
    assert result['skipped'] == 1 and moodle['downloads'] == []
    assert not db.scalar(select(Item).where(Item.kind=='document'))

@pytest.mark.parametrize('status', [302,403,429])
def test_download_failures_do_not_disclose_token(db,user,moodle,status):
    connector = connected(db,user); moodle['download_status'] = status
    with pytest.raises(HTTPException) as error: uv.university_sync(db,user,connector)
    assert TOKEN not in str(error.value.detail) and 'token=' not in str(error.value.detail)
    assert not db.scalar(select(Item).where(Item.kind=='document'))


def test_api_exception_does_not_save_credentials_or_expose_secret(db,user,moodle):
    moodle['api_error'] = True
    with pytest.raises(HTTPException) as error: uv.configure_university(db,user,TOKEN)
    assert TOKEN not in str(error.value.detail)
    assert not db.get(Connector,('university',user.id))


def test_public_target_rejects_private_dns_and_other_origin(monkeypatch):
    monkeypatch.setattr(uv.socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    for url in (uv.BASE_URL+'/webservice/rest/server.php','https://evil.example/file','http://aulavirtual.uv.es/file'):
        with pytest.raises(HTTPException): uv.public_target(url)


def test_stream_size_bound_applies_even_without_content_length(db,user,moodle,monkeypatch):
    connector = connected(db,user)
    moodle['files'][0]['filesize'] = 0
    monkeypatch.setattr(uv,'MAX_FILE_BYTES',5)
    result = uv.university_sync(db,user,connector)
    assert result['skipped'] == 1 and not db.scalar(select(Item).where(Item.kind=='document'))

from test_api import client, auth


def browser_payload(**changes):
    return {'course_title':'Química orgánica', 'course_url':uv.BASE_URL+'/course/view.php?id=7&token=never-store',
            'resource_url':uv.BASE_URL+'/mod/resource/view.php?id=99&token=never-store', **changes}


def test_browser_import_requires_authentication(client):
    response=client.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'Contenido')})
    assert response.status_code==401


def test_browser_import_owns_blob_sanitizes_urls_and_is_idempotent(auth):
    from app.database import SessionLocal
    first=auth.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'Contenido')})
    assert first.status_code==201 and first.json()['created'] and first.json()['extracted']
    item_id=first.json()['id'];resource=auth.get('/api/v1/items/'+item_id).json()
    assert resource['description']=='Contenido' and resource['area']=='university'
    assert resource['source']=='university-browser' and 'never-store' not in json.dumps(resource)
    assert resource['data']['url']==uv.BASE_URL+'/mod/resource/view.php?id=99'
    assert resource['data']['course_url']==uv.BASE_URL+'/course/view.php?id=7'
    download=auth.get('/api/v1/documents/'+item_id+'/download')
    assert download.status_code==200 and download.content==b'Contenido'
    repeat=auth.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'Contenido')})
    assert repeat.json()=={'id':item_id,'created':False,'unchanged':True}
    changed=auth.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'Nuevo')})
    assert changed.json()['id']==item_id and not changed.json()['created'] and not changed.json()['unchanged']
    with SessionLocal() as session:
        assert session.get(Item,item_id).version==2
        other=User(email='browser-other@fixture',password_hash='fixture');session.add(other);session.flush()
        from app.security import issue_session
        from fastapi import Response
        credentials=issue_session(session,other,Response());session.commit()
    other_headers={'Authorization':'Bearer '+credentials['access_token']}
    assert auth.get('/api/v1/documents/'+item_id+'/download',headers=other_headers).status_code==404
    another=auth.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'Private')},headers=other_headers)
    assert another.status_code==201 and another.json()['id']!=item_id
    assert auth.get('/api/v1/documents/'+item_id+'/download').content==b'Nuevo'


@pytest.mark.parametrize('changes', [
    {'course_url':'https://evil.example/course/view.php?id=7'},
    {'resource_url':'https://evil.example/pluginfile.php/99/file.txt'},
    {'resource_url':'http://aulavirtual.uv.es/pluginfile.php/99/file.txt'},
    {'course_url':uv.BASE_URL+'/course/view.php?id=abc'},
    {'resource_url':uv.BASE_URL+'/login/index.php'},
])
def test_browser_import_rejects_foreign_or_invalid_source(auth,changes):
    response=auth.post('/api/v1/connectors/university/import',data=browser_payload(**changes),files={'file':('Apuntes.txt',b'Contenido')})
    assert response.status_code==422


def test_browser_import_enforces_file_bound(auth,monkeypatch):
    monkeypatch.setattr(uv,'MAX_BROWSER_BYTES',5)
    response=auth.post('/api/v1/connectors/university/import',data=browser_payload(),files={'file':('Apuntes.txt',b'123456')})
    assert response.status_code==413


def office_archive(files):
    import io
    from zipfile import ZipFile,ZIP_DEFLATED
    result=io.BytesIO()
    with ZipFile(result,'w',compression=ZIP_DEFLATED) as archive:
        for name,content in files.items():archive.writestr(name,content)
    return result.getvalue()


@pytest.mark.parametrize('extension,files,expected', [
    ('.docx',{'word/document.xml':'<document><t>Química</t><t>orgánica</t></document>'},'Química orgánica'),
    ('.pptx',{'ppt/slides/slide10.xml':'<slide><t>Segunda</t></slide>','ppt/slides/slide2.xml':'<slide><t>Primera</t></slide>'},'Primera\nSegunda'),
    ('.xlsx',{'xl/sharedStrings.xml':'<sst><si><t>Materia</t></si></sst>',
              'xl/worksheets/sheet1.xml':'<worksheet><c t="s"><v>0</v></c><c><v>42</v></c><c t="inlineStr"><is><t>Nota</t></is></c></worksheet>'},'Materia\t42\tNota'),
    ('.odt',{'content.xml':'<document><p>Temario</p></document>'},'Temario'),
])
def test_office_extracts_bounded_text(extension,files,expected):
    text,warning,partial=uv.extract_document(office_archive(files),extension)
    assert text==expected and warning is None and not partial


def test_office_extraction_rejects_entities_and_zip_expansion():
    dangerous='<!DOCTYPE document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><document><t>&xxe;</t></document>'
    for files in ({'word/document.xml':dangerous},{'word/document.xml':'<document/>','big.bin':b'0'*20_000_001}):
        text,warning,partial=uv.extract_document(office_archive(files),'.docx')
        assert text=='' and warning and partial


def test_extracted_text_removes_postgres_nul_characters():
    text,warning,partial=uv.extract_document(b'Apuntes\x00universitarios','.txt')
    assert text=='Apuntesuniversitarios'


def test_authorized_browser_upload_has_separate_body_limit(auth):
    payload=browser_payload()
    response=auth.post('/api/v1/connectors/university/import',data=payload,files={'file':('Limite.txt',b'Apuntes')},headers={'content-length':'12000000'})
    assert response.status_code==201
    ordinary=auth.post('/api/v1/documents/upload',data={'area':'university'},files={'file':('Limite.txt',b'Apuntes')},headers={'content-length':'12000000'})
    assert ordinary.status_code==413
