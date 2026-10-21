from datetime import datetime
import pytest
from sqlalchemy import select,event
from app.database import SessionLocal,Item,User
from test_api import client,auth


def add(rows):
    with SessionLocal() as db:
        owner=db.scalar(select(User).where(User.email=='aros@local'))
        for title,start,data,extra in rows:db.add(Item(user_id=owner.id,kind='event',area='learning',title=title,due_at=start,data=data,**extra))
        db.commit()

@pytest.mark.parametrize('query',['start=2026-12-01&end=2026-12-01','start=2026-12-02&end=2026-12-01','start=2026-12-01&end=2027-02-02','start=bad&end=2026-12-02','start=2026-12-01&end=2026-12-02&zone=Invalid/Zone','start=2026-12-01&end=2026-12-02&zone=../Europe/Madrid'])
def test_invalid_calendar_range(auth,query):assert auth.get('/api/v1/calendar/range?'+query).status_code==422

def test_calendar_max_span_and_auth(auth):
    assert auth.get('/api/v1/calendar/range?start=2026-12-01&end=2027-02-01').status_code==200
    auth.post('/api/v1/auth/logout')
    assert auth.get('/api/v1/calendar/range?start=2026-12-01&end=2026-12-02').status_code==401

def test_calendar_intersection_order_boundaries_and_daily(auth):
    add([('Midnight','2026-11-30T22:30:00Z',{'end_at':'2026-12-01T00:30:00Z'},{}),('Boundary end','2026-11-30T21:00:00Z',{'end_at':'2026-11-30T23:00:00Z'},{}),('Boundary start','2026-12-01T23:00:00Z',{'end_at':'2026-12-02T00:00:00Z'},{}),('Inside','2026-12-01T09:00:00Z',{},{}),('Long','2026-11-25T00:00:00Z',{'end_at':'2026-12-05T00:00:00Z'}, {})])
    result=auth.get('/api/v1/calendar/range?start=2026-12-01&end=2026-12-02').json()
    assert (result['start'],result['end'],result['timezone'])==('2026-12-01','2026-12-02','Europe/Madrid')
    assert [e['title'] for e in result['events']]==['Long','Midnight','Inside']
    assert result['events']==auth.get('/api/v1/calendar/day?day=2026-12-01').json()['events']

def test_calendar_all_day_dst_and_exclusive_dates(auth):
    add([('DST','2027-03-28',{'all_day':True,'start_date':'2027-03-28','end_at':'2027-03-29'},{}),('Multi','2027-03-27',{'all_day':True,'end_at':'2027-03-30'}, {})])
    full=next(e for e in auth.get('/api/v1/calendar/range?start=2027-03-28&end=2027-03-29').json()['events'] if e['title']=='DST')
    assert full['all_day'] and not full['estimated_duration']
    assert (datetime.fromisoformat(full['end'])-datetime.fromisoformat(full['start'])).total_seconds()==23*3600
    assert [e['title'] for e in auth.get('/api/v1/calendar/range?start=2027-03-29&end=2027-03-30').json()['events']]==['Multi']
    assert auth.get('/api/v1/calendar/range?start=2027-03-30&end=2027-03-31').json()['events']==[]
    other=auth.get('/api/v1/calendar/range?start=2027-03-28&end=2027-03-29&zone=America/Los_Angeles').json()
    assert next(e for e in other['events'] if e['title']=='DST')['start']=='2027-03-28T07:00:00+00:00'

def test_calendar_owner_status_malformed_and_metadata(auth):
    add([('Exam','2026-12-01T09:00:00Z',{'category':'exam','location':'Aula4'},{'source':'manual'}),('Lab','2026-12-01T10:00:00Z',{'session_type':'laboratory','event_type':'class','location':'Lab2'},{'source':'manual','is_demo':True}),('Broken','corrupt',{},{}),('Naive','2026-12-01T09:00:00',{},{}),('Done','2026-12-01T11:00:00Z',{}, {'status':'done'}),*[(s,'2026-12-01T09:00:00Z',{}, {'status':s}) for s in ['cancelled','canceled','archived']]])
    with SessionLocal() as db:
        u=User(email='private-range@local',password_hash='unused');db.add(u);db.flush();db.add(Item(user_id=u.id,kind='event',area='personal',title='Secret',due_at='2026-12-01T10:00:00Z'));db.commit()
    events=auth.get('/api/v1/calendar/range?start=2026-12-01&end=2026-12-02').json()['events']
    assert [e['title'] for e in events]==['Exam','Lab','Done']
    assert (events[0]['category'],events[0]['location'],events[0]['source'],events[0]['is_demo'])==('exam','Aula4','manual',False)
    assert (events[1]['category'],events[1]['location'],events[1]['is_demo'])==('laboratory','Lab2',True)
    assert len(auth.get('/api/v1/calendar/day?day=2026-12-01').json()['events'])==3

def test_calendar_single_event_query_with_permission_check_defers_blob(auth):
    from app import database
    statements=[]
    def record(conn,cursor,statement,parameters,context,executemany):
        if 'FROM items' in statement:statements.append(statement)
    event.listen(database.engine,'before_cursor_execute',record)
    try:assert auth.get('/api/v1/calendar/range?start=2026-12-01&end=2027-02-01').status_code==200
    finally:event.remove(database.engine,'before_cursor_execute',record)
    # One event query for the 62-day range plus the lightweight provenance
    # permission query. Never one request/query per day or document blob loads.
    assert len([statement for statement in statements if 'items.kind = ?' in statement])==1
    assert len(statements)<=3 and all('items.blob' not in statement for statement in statements)


def test_calendar_fall_dst_and_estimated_elapsed_hour(auth):
    add([('Fall day','2026-10-25',{'all_day':True,'end_at':'2026-10-26'},{}),('One hour','2027-03-28T01:30:00+01:00',{}, {})])
    fall=auth.get('/api/v1/calendar/range?start=2026-10-25&end=2026-10-26').json()['events'][0]
    assert (datetime.fromisoformat(fall['end'])-datetime.fromisoformat(fall['start'])).total_seconds()==25*3600
    spring=auth.get('/api/v1/calendar/range?start=2027-03-28&end=2027-03-29').json()['events'][0]
    assert spring['estimated_duration']
    assert (datetime.fromisoformat(spring['end'])-datetime.fromisoformat(spring['start'])).total_seconds()==3600
