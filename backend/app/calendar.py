"""Owner-scoped range and daily schedule, independent from calendar providers."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import defer
from .database import Item, User, get_db
from .security import current_user
from .source_access import visible_items_clause

router=APIRouter(prefix='/api/v1/calendar',tags=['Calendar'])


def calendar_zone(zone):
    try: return ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError): raise HTTPException(422,'Zona horaria desconocida') from None


def event_interval(item,tz):
    data=item.data or {}
    assumed=False
    try:
        if data.get('all_day'):
            start_date=date.fromisoformat(str(data.get('start_date') or item.due_at[:10]))
            start=datetime.combine(start_date,time.min,tz).astimezone(timezone.utc)
            end_date=date.fromisoformat(str(data['end_at'])[:10]) if data.get('end_at') else start_date+timedelta(days=1)
            end=datetime.combine(end_date,time.min,tz).astimezone(timezone.utc)
            if end<=start:return None
        else:
            start=datetime.fromisoformat(item.due_at.replace('Z','+00:00'))
            if start.tzinfo is None:return None
            start=start.astimezone(timezone.utc)
            try:
                end=datetime.fromisoformat(str(data['end_at']).replace('Z','+00:00'))
                if end.tzinfo is None:raise ValueError('Missing timezone')
                end=end.astimezone(timezone.utc)
                if end<=start:raise ValueError('Invalid interval')
            except (KeyError,ValueError,TypeError,OverflowError):
                end=start+timedelta(hours=1);assumed=True
    except (ValueError,TypeError,OverflowError):
        # Malformed provider metadata must not crash the whole calendar.
        # A valid timestamp remains occupied for an estimated elapsed hour.
        try:
            start=datetime.fromisoformat(item.due_at.replace('Z','+00:00'))
            if start.tzinfo is None:return None
            start=start.astimezone(timezone.utc);end=start+timedelta(hours=1);assumed=True
        except (ValueError,TypeError,OverflowError):return None
    category=str(data.get('session_type') or data.get('event_type') or data.get('category') or '')
    if not category and item.source=='uv-calendar':category='exam' if data.get('exam') else 'class'
    return {'id':item.id,'title':item.title,'area':item.area,'start':start,'end':end,
            'all_day':bool(data.get('all_day')),'estimated_duration':assumed,
            'location':str(data.get('location') or ''),'category':category or 'event',
            'source':item.source or '', 'is_demo':bool(item.is_demo)}


def events_between(db,user,local_start,local_end,tz):
    begin=local_start.astimezone(timezone.utc);finish=local_end.astimezone(timezone.utc)
    events=[]
    # One owner-scoped query for the whole range, including earlier starts whose
    # provider end may extend into it. Do not fetch document binary payloads.
    # Conversation-created events can depend on copied remote-source data.
    # Apply the same dependency/version policy as the library and agent reads.
    query=select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.kind=='event',Item.due_at.is_not(None),Item.status.notin_(['cancelled','canceled','archived']),Item.source.notin_(['agent-execution','agent-selection','automation-result']),visible_items_clause(db,user))
    for item in db.scalars(query):
        event=event_interval(item,tz)
        if event and event['start']<finish and event['end']>begin:events.append(event)
    events.sort(key=lambda event:(event['start'],event['id']))
    return events


def serialize_events(events):
    return [{**event,'start':event['start'].isoformat(),'end':event['end'].isoformat()} for event in events]


def daily_plan(db,user,day,zone,start_hour,end_hour,min_minutes):
    tz=calendar_zone(zone)
    if start_hour>=end_hour: raise HTTPException(422,'La hora de inicio debe ser anterior a la de fin')
    # Local midnight boundaries are constructed independently, including DST days.
    local_start=datetime.combine(day,time.min,tz)
    local_end=datetime.combine(day+timedelta(days=1),time.min,tz)
    window_start=datetime.combine(day,time(start_hour),tz).astimezone(timezone.utc)
    window_end=(local_end if end_hour==24 else datetime.combine(day,time(end_hour),tz)).astimezone(timezone.utc)
    events=events_between(db,user,local_start,local_end,tz)
    conflicts=[]
    for index,event in enumerate(events):
        for other in events[index+1:]:
            if other['start']>=event['end']: break
            begin=max(event['start'],other['start'],local_start.astimezone(timezone.utc))
            finish=min(event['end'],other['end'],local_end.astimezone(timezone.utc))
            if begin<finish: conflicts.append({'ids':[event['id'],other['id']],'start':begin.isoformat(),'end':finish.isoformat()})
    occupied=sorted((max(event['start'],window_start),min(event['end'],window_end)) for event in events if event['start']<window_end and event['end']>window_start)
    cursor=window_start;free=[]
    for begin,finish in occupied+[(window_end,window_end)]:
        duration=int((begin-cursor).total_seconds()/60)
        if duration>=min_minutes: free.append({'start':cursor.isoformat(),'end':begin.isoformat(),'minutes':duration})
        cursor=max(cursor,finish)
    return {'date':day.isoformat(),'timezone':zone,'start_hour':start_hour,'end_hour':end_hour,'events':serialize_events(events),'conflicts':conflicts,'free_slots':free,'note':'Disponibilidad según los eventos guardados en Aros. Sin hora de fin se estima una duración de 60 minutos; otras agendas pueden no estar sincronizadas.'}

@router.get('/day')
def schedule(user:Annotated[User,Depends(current_user)],db:Annotated[object,Depends(get_db)],day:date|None=None,zone:str=Query(default='Europe/Madrid',max_length=80),start_hour:int=Query(default=8,ge=0,le=23),end_hour:int=Query(default=22,ge=1,le=24),min_minutes:int=Query(default=30,ge=15,le=720)):
    tz=calendar_zone(zone)
    return daily_plan(db,user,day or datetime.now(tz).date(),zone,start_hour,end_hour,min_minutes)


@router.get('/range')
def calendar_range(user:Annotated[User,Depends(current_user)],db:Annotated[object,Depends(get_db)],start:date,end:date,zone:str=Query(default='Europe/Madrid',max_length=80)):
    tz=calendar_zone(zone)
    span=(end-start).days
    if not 1<=span<=62:raise HTTPException(422,'El rango debe contener entre 1 y 62 días; la fecha final es exclusiva')
    begin=datetime.combine(start,time.min,tz);finish=datetime.combine(end,time.min,tz)
    return {'start':start.isoformat(),'end':end.isoformat(),'timezone':zone,'events':serialize_events(events_between(db,user,begin,finish,tz))}
