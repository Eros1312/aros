from datetime import datetime, timezone, timedelta
from app.database import SessionLocal, Item, Inbox, User, Connector, Chat, now
from app.security import hash_password
from app.ai import context_items, recent_history
from app.source_access import resource_fingerprint
from test_api import client, auth


def test_workspace_requires_auth(client):
    assert client.get('/api/v1/workspace/summary').status_code == 401
    assert client.get('/api/v1/workspace/library').status_code == 401


def test_priorities_do_not_count_imported_documents_and_preserve_sync_time(auth):
    with SessionLocal() as db:
        user=db.query(User).first()
        task=Item(user_id=user.id,kind='task',area='prisma',title='Pendiente real',status='pending',priority='urgent')
        doc=Item(user_id=user.id,kind='document',area='university',source='university-browser',title='Tema importado',status='active')
        db.add_all([task,doc]);db.flush()
        db.add(Inbox(user_id=user.id,item_id=doc.id,title=doc.title,reason='Documento importado',area='university'))
        weather=db.get(Connector,('weather',user.id));weather.last_sync='2025-01-02T12:00:00+00:00';db.commit()
        task_id=task.id;doc_id=doc.id
    a=auth.get('/api/v1/workspace/summary').json()
    b=auth.get('/api/v1/workspace/summary').json()
    assert len(a['priorities'])<=3 and task_id in [p['item_id'] for p in a['priorities']]
    assert doc_id not in [p['item_id'] for p in a['priorities']]
    assert a['attention']['excluded_non_actionable_count']>=1
    assert next(c for c in a['coverage'] if c['id']=='weather')['last_successful_sync_at']=='2025-01-02T12:00:00+00:00'
    assert [c['last_successful_sync_at'] for c in a['coverage']]==[c['last_successful_sync_at'] for c in b['coverage']]


def test_library_search_before_pagination_and_owner_scope(auth):
    with SessionLocal() as db:
        user=db.query(User).first()
        other=User(email='outside@local',password_hash=hash_password('long-enough-outside'));db.add(other);db.flush()
        foreign=Item(user_id=other.id,kind='document',area='university',title='Ácido oculto',status='active')
        found=Item(user_id=user.id,kind='document',area='university',title='Equilibrio ácido',description='Una fórmula de muestra y otra referencia',status='active',source='university-browser',data={'course_title':'Química analítica','sha256':'example','partial':True})
        db.add_all([foreign,found]);db.commit();wanted=found.id;secret=foreign.id
    data=auth.get('/api/v1/workspace/library?q=acido&limit=1').json()
    assert data['pagination']['total']==1 and data['items'][0]['id']==wanted
    assert data['items'][0]['library']['group']=='Química analítica'
    assert data['items'][0]['library']['partial'] and data['items'][0]['library']['original']
    assert secret not in [i['id'] for i in auth.get('/api/v1/workspace/library').json()['items']]
    assert auth.get('/api/v1/workspace/library?limit=101').status_code==422
    assert auth.get('/api/v1/workspace/library?area=wrong').status_code==422


def test_approved_filter_requires_explicit_metadata(auth):
    with SessionLocal() as db:
        user=db.query(User).first()
        final=Item(user_id=user.id,kind='document',area='prisma',title='final_definitivo.pdf',status='active',source='prisma-file',data={'review_state':'original'})
        approved=Item(user_id=user.id,kind='document',area='prisma',title='versión revisada.pdf',status='active',source='prisma-file',data={'review_state':'approved'})
        db.add_all([final,approved]);db.commit();wanted=approved.id
    data=auth.get('/api/v1/workspace/library?area=prisma&approved=true').json()
    assert [i['id'] for i in data['items']]==[wanted]


def test_revoked_source_and_transitive_derivatives_unavailable_but_local_original_preserved(auth):
    with SessionLocal() as db:
        user=db.query(User).first();conn=db.get(Connector,('gmail',user.id));conn.status='connected'
        mail=Item(user_id=user.id,kind='document',area='personal',source='gmail',title='Privado revocable',description='Email body',status='active')
        local=Item(user_id=user.id,kind='document',area='university',source='university-browser',title='Original autorizado',status='active',blob=b'original')
        db.add_all([mail,local]);db.flush()
        notice=Inbox(user_id=user.id,item_id=mail.id,title=mail.title,reason='Mail imported',area='personal')
        db.add(notice);db.flush();notice_id=notice.id
        first=Item(user_id=user.id,kind='note',area='personal',source='agent-execution',title='Extracto derivado',description='Copied email',status='active',data={'source_references':[{'id':mail.id,'version':1}]})
        db.add(first);db.flush()
        second=Item(user_id=user.id,kind='note',area='personal',source='agent-execution',title='Derivado del derivado',description='Copied email again',status='active',data={'source_references':[{'id':first.id,'version':1}]})
        db.add(second);db.commit();ids=[mail.id,first.id,second.id];local_id=local.id
    assert set(ids)<=set(i['id'] for i in auth.get('/api/v1/workspace/library').json()['items'])
    with SessionLocal() as db:
        user=db.query(User).first();db.get(Connector,('gmail',user.id)).status='disconnected';db.commit()
        assert not set(ids)&{i.id for i in context_items(db,user,'Privado revocable')}
    for ident in ids:assert auth.get('/api/v1/items/'+ident).status_code==404
    assert not set(ids)&{i['id'] for i in auth.get('/api/v1/workspace/library').json()['items']}
    assert not any(i['item_id'] in ids for i in auth.get('/api/v1/inbox').json())
    assert auth.patch('/api/v1/inbox/'+notice_id,json={'state':'seen'}).status_code==404
    assert not any(i['title']=='Privado revocable' for i in auth.get('/api/v1/dashboard').json()['attention'])
    assert auth.get('/api/v1/documents/'+local_id+'/download').content==b'original'


def test_revoked_excerpt_not_reused_as_chat_history(auth):
    with SessionLocal() as db:
        user=db.query(User).first();conn=db.get(Connector,('gmail',user.id));conn.status='connected'
        mail=Item(user_id=user.id,kind='email',area='personal',source='gmail',title='Private mail',status='active')
        db.add(mail);db.flush()
        message=Chat(user_id=user.id,role='assistant',text='Private excerpt',sources=[{'id':mail.id,'title':mail.title,'area':'personal','kind':'email'}])
        db.add(message);db.commit()
        assert any(m.text=='Private excerpt' for m in recent_history(db,user))
        conn.status='disconnected';db.commit()
        assert not any(m.text=='Private excerpt' for m in recent_history(db,user))
    assert not any(m['text']=='Private excerpt' for m in auth.get('/api/v1/chat').json())


def test_content_hash_invalidates_derivative_even_without_version_bump(auth):
    with SessionLocal() as db:
        user=db.query(User).first()
        original=Item(user_id=user.id,kind='document',area='prisma',source='prisma-file',title='Original',description='First content',status='active')
        db.add(original);db.flush()
        derived=Item(user_id=user.id,kind='note',area='prisma',source='agent-execution',title='Extract',description='First content',status='active',data={'source_references':[{'id':original.id,'version':original.version,'content_hash':resource_fingerprint(original)}]})
        db.add(derived);db.commit();ident=derived.id
    assert auth.get('/api/v1/items/'+ident).status_code==200
    with SessionLocal() as db:
        original=db.get(Item,original.id);original.description='Changed content';db.commit()
    assert auth.get('/api/v1/items/'+ident).status_code==404


def test_daily_digest_keeps_provenance_and_hides_revoked_material(auth):
    from app.worker import tick
    with SessionLocal() as db:
        user=db.query(User).first();db.get(Connector,('gmail',user.id)).status='connected'
        record=Item(user_id=user.id,kind='task',area='personal',source='gmail',title='Private copied task',status='pending',priority='urgent')
        db.add(record);db.commit();ident=record.id
    a=auth.post('/api/v1/automations',json={'title':'Digest isolated test','kind':'daily_digest','enabled':False,'interval_minutes':1440}).json()
    auth.post('/api/v1/automations/'+a['id']+'/run');tick()
    with SessionLocal() as db:
        note=db.query(Item).filter(Item.source=='automation-result').first();note_id=note.id
        assert any(r['id']==ident for r in note.data['source_references'])
        user=db.query(User).first();db.get(Connector,('gmail',user.id)).status='disconnected';db.commit()
    assert auth.get('/api/v1/items/'+note_id).status_code==404
    assert not any(i['id']==note_id for i in auth.get('/api/v1/workspace/library').json()['items'])


def test_deadline_review_excludes_revoked_source_from_notices_and_count(auth):
    from app.database import Automation, Run
    from app.worker import tick
    with SessionLocal() as db:
        user=db.query(User).first()
        for automation in db.query(Automation).filter(Automation.user_id==user.id):
            automation.enabled=False
        for existing in db.query(Item).filter(Item.user_id==user.id):
            existing.status='archived'
        db.get(Connector,('gmail',user.id)).status='disconnected'
        due=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
        revoked=Item(user_id=user.id,kind='task',area='personal',source='gmail',title='Revoked deadline fixture',status='pending',due_at=due)
        local=Item(user_id=user.id,kind='task',area='personal',source='manual',title='Local deadline fixture',status='pending',due_at=due)
        db.add_all([revoked,local]);db.commit();revoked_id=revoked.id;local_id=local.id
    response=auth.post('/api/v1/automations',json={'title':'Deadline isolated test','kind':'deadline_review','enabled':False,'interval_minutes':1440})
    assert response.status_code==201
    automation_id=response.json()['id']
    assert auth.post('/api/v1/automations/'+automation_id+'/run').status_code==202
    tick()
    with SessionLocal() as db:
        result=db.query(Run).filter(Run.automation_id==automation_id).one()
        assert result.status=='completed'
        assert result.result=='1 entregas o recordatorios próximos revisados.'
        assert not db.query(Inbox).filter(Inbox.item_id==revoked_id).first()
        assert db.query(Inbox).filter(Inbox.item_id==local_id).count()==1
