"""Original synthetic PDF grids; all network operations are mocked."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import hashlib
import pytest,pymupdf
from fastapi import HTTPException
from sqlalchemy import select,func
from test_api import client,auth
from app import university_calendar as uv
from app.database import SessionLocal,User,Item,Connector,Automation,Run,now
from app.config import settings

from synthetic_calendar import synthetic_timetable, synthetic_exams, CLASS_COUNT, EXAM_COUNT, TOTAL_COUNT

@pytest.fixture(scope='module')
def synthetic():
    timetable=synthetic_timetable()
    exams=synthetic_exams()
    return timetable,exams,uv.parse_timetable(timetable),uv.parse_timetable(exams,True)

@pytest.fixture
def offline(monkeypatch,synthetic):
    timetable,exams,classes,exam_events=synthetic
    monkeypatch.setattr(uv,'read_pdf',lambda url:{uv.SOURCE:timetable,uv.EXAMS:exams}[url])
    monkeypatch.setattr(uv,'parse_timetable',lambda raw,exams=False:deepcopy(exam_events if exams else classes))
    return synthetic

def owner(db):return db.scalar(select(User).where(User.email==settings.admin_email))

def test_synthetic_counts_hours_dst_group_and_exam_coordinates(synthetic):
    timetable,exams,classes,exam_events=synthetic
    assert len(classes)==CLASS_COUNT and len(exam_events)==EXAM_COUNT
    first=classes[0]
    assert first['date']=='2026-09-14' and first['title']=='Bioquímica'
    assert first['start']=='2026-09-14T06:30:00+00:00' and first['end']=='2026-09-14T08:30:00+00:00'
    assert first['location']=='Aula Demo 1'
    assert classes[1]['date']=='2026-09-16' and classes[1]['start']=='2026-09-16T12:00:00+00:00'
    assert classes[-1]['start']=='2027-04-12T08:15:00+00:00'
    zone=ZoneInfo('Europe/Madrid')
    offsets={datetime.fromisoformat(event['start']).astimezone(zone).utcoffset() for event in classes}
    assert offsets=={timedelta(hours=1),timedelta(hours=2)}
    for event in classes+exam_events:
        local=datetime.fromisoformat(event['start']).astimezone(zone)
        end=datetime.fromisoformat(event['end']).astimezone(zone)
        assert local.date().isoformat()==event['date']
        assert 7<=local.hour<21 and end>local and local.minute%15==end.minute%15==0
    winter=next(event for event in classes if event['date']=='2026-11-02')
    summer=next(event for event in classes if event['date']=='2027-04-12')
    assert datetime.fromisoformat(winter['start']).astimezone(zone).utcoffset()==timedelta(hours=1)
    assert datetime.fromisoformat(summer['start']).astimezone(zone).utcoffset()==timedelta(hours=2)
    assert exam_events[0]['label']=='Lab. Q. Orgànica II EC'
    assert exam_events[0]['start']=='2026-10-29T14:00:00+00:00'
    assert not any('Bioquímica B' in event['label'] for event in exam_events)
    assert not any('EV' in event['label'] and 'EC' not in event['label'] for event in exam_events)

def test_wrong_group_and_exam_year_fail_closed(synthetic):
    timetable,exams,*_=synthetic
    def replace(raw,phrase,replacement):
        document=pymupdf.open(stream=raw,filetype='pdf');page=document[0]
        boxes=page.search_for(phrase);assert boxes
        box=boxes[0];page.add_redact_annot(box,fill=(1,1,1));page.apply_redactions()
        page.insert_text((box.x0,box.y1-2),replacement,fontsize=8)
        changed=document.tobytes();document.close();return changed
    with pytest.raises(ValueError):uv.parse_timetable(replace(timetable,'GRUP EC','GRUP EV'))
    with pytest.raises(ValueError):uv.parse_timetable(replace(exams,'octubre 2026','octubre 2025'),True)

def test_sync_idempotence_originals_sources_and_export(auth,offline):
    first=auth.post('/api/v1/calendar/university/sync',json={});assert first.status_code==200,first.text
    assert first.json()['created']==TOTAL_COUNT
    second=auth.post('/api/v1/calendar/university/sync',json={});assert second.status_code==200
    assert second.json()['created']==second.json()['updated']==0
    with SessionLocal() as db:
        user=owner(db)
        assert db.scalar(select(func.count()).select_from(Item).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.kind=='event',Item.status=='active'))==TOTAL_COUNT
        documents=list(db.scalars(select(Item).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.kind=='document')))
        assert len(documents)==2
        content={item.data['filename']:item.blob for item in documents}
        assert content['UV-4-EC-2026-27.pdf']==offline[0]
        assert content['UV-examenes-2026-27.pdf']==offline[1]
        for document in documents:assert document.data['sha256']==hashlib.sha256(document.blob).hexdigest()
        events=list(db.scalars(select(Item).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.kind=='event')))
        for event in events:
            assert event.data['group']=='EC' and event.data['timezone']=='Europe/Madrid'
            expected=offline[0] if event.data['source_url']==uv.SOURCE else offline[1]
            assert event.data['source_sha256']==hashlib.sha256(expected).hexdigest()
    exported=auth.get('/api/v1/calendar/university/export');assert exported.status_code==200
    assert exported.text.count('BEGIN:VEVENT')==TOTAL_COUNT
    assert 'DTSTART:20260914T063000Z' in exported.text
    assert all(len(line.encode())<=75 for line in exported.text.splitlines())

def test_failure_preserves_every_previous_event_and_both_originals(auth,offline,monkeypatch):
    assert auth.post('/api/v1/calendar/university/sync',json={}).status_code==200
    with SessionLocal() as db:
        user=owner(db);snapshot=[(item.id,item.version,item.status,item.blob,item.data) for item in db.scalars(select(Item).where(Item.user_id==user.id,Item.source=='uv-calendar'))]
    def fail(raw,exams=False):raise ValueError('unknown grid')
    monkeypatch.setattr(uv,'parse_timetable',fail)
    assert auth.post('/api/v1/calendar/university/sync',json={}).status_code==502
    with SessionLocal() as db:
        user=owner(db)
        assert [(item.id,item.version,item.status,item.blob,item.data) for item in db.scalars(select(Item).where(Item.user_id==user.id,Item.source=='uv-calendar'))]==snapshot
        row=db.get(Connector,('university-calendar',user.id));assert row.status=='error' and row.sync_details['count']==TOTAL_COUNT

@pytest.mark.parametrize('desired',['paused','disconnected'])
def test_concurrent_pause_or_disconnect_discards_import_without_rebound(auth,offline,monkeypatch,desired):
    original_read=uv.read_pdf
    with SessionLocal() as db:
        user=owner(db)
        def read(url):
            if url==uv.SOURCE:
                if desired=='paused':response=auth.patch('/api/v1/connectors/university-calendar',json={'status':'paused'})
                else:
                    token=auth.post('/api/v1/confirmations',json={'action':'connector.disconnect','payload':{'id':'university-calendar'}}).json()['token']
                    response=auth.delete('/api/v1/connectors/university-calendar',headers={'X-Confirmation':token})
                assert response.status_code==200,response.text
            return original_read(url)
        monkeypatch.setattr(uv,'read_pdf',read)
        with pytest.raises(HTTPException) as failure:uv.sync_uv_calendar(db,user)
        assert failure.value.status_code==409
        db.expire_all();row=db.get(Connector,('university-calendar',user.id));assert row.status==desired
        assert db.scalar(select(func.count()).select_from(Item).where(Item.user_id==user.id,Item.source=='uv-calendar'))==0

def test_simultaneous_sync_is_rejected_and_paused_skips_download(auth,offline,monkeypatch):
    original_read=uv.read_pdf;reads=[]
    def read(url):
        reads.append(url)
        with SessionLocal() as db:
            with pytest.raises(HTTPException) as failure:uv.sync_uv_calendar(db,owner(db))
            assert failure.value.status_code==409
        return original_read(url)
    monkeypatch.setattr(uv,'read_pdf',read)
    assert auth.post('/api/v1/calendar/university/sync',json={}).status_code==200
    assert auth.patch('/api/v1/connectors/university-calendar',json={'status':'paused'}).status_code==200
    reads.clear();assert auth.post('/api/v1/calendar/university/sync',json={}).status_code==409;assert reads==[]

def test_source_events_and_pdfs_are_isolated_between_users(auth,offline):
    assert auth.post('/api/v1/calendar/university/sync',json={}).status_code==200
    with SessionLocal() as db:
        other=User(email='uv-isolated@example.test',password_hash='unused');db.add(other);db.commit()
        assert uv.status(other,db)['count']==0
        exported=uv.export(other,db);assert b'BEGIN:VEVENT' not in exported.body
        result=uv.sync_uv_calendar(db,other);assert result['created']==TOTAL_COUNT
        first=owner(db)
        assert db.scalar(select(func.count()).select_from(Item).where(Item.user_id==other.id,Item.source=='uv-calendar'))==TOTAL_COUNT+2
        assert db.scalar(select(func.count()).select_from(Item).where(Item.user_id==first.id,Item.source=='uv-calendar'))==TOTAL_COUNT+2

def test_daily_automation_runs_calendar_import_instead_of_creating_generic_note(auth,offline,monkeypatch):
    from app.worker import tick
    monkeypatch.setattr('app.channels.rpc',lambda method,params:{'hash':'offline-test'})
    with SessionLocal() as db:
        user=owner(db)
        # Existing demo automations do not matter for this explicit calendar job.
        automation=Automation(user_id=user.id,title='Horario UV diario',kind='uv_calendar_sync',enabled=False)
        db.add(automation);db.flush();job=Run(user_id=user.id,automation_id=automation.id,origin='manual');db.add(job);db.commit();job_id=job.id
    tick()
    with SessionLocal() as db:
        result=db.get(Run,job_id);assert result.status=='completed'
        assert f'{CLASS_COUNT} clases y {EXAM_COUNT} exámenes' in result.result
