"""Calendar cannot expose copied revoked sources through generated events."""
import pytest
from test_api import client,auth
from app.database import SessionLocal,Item,User,Connector
from app.source_access import resource_fingerprint
from sqlalchemy import select

@pytest.mark.parametrize('change',['revoked','changed'])
def test_derived_event_hides_after_revocation_or_content_change_manual_session_preserved(auth,change):
    marker='PRIVATE_CALENDAR_DERIVED_734'
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        connector=db.get(Connector,('gmail',user.id));connector.status='connected'
        source=Item(user_id=user.id,kind='email',area='personal',source='gmail',title='Private source',description=marker,status='active');db.add(source);db.flush()
        reference={'id':source.id,'version':source.version,'content_hash':resource_fingerprint(source)}
        derived=Item(user_id=user.id,kind='event',area='personal',source='agent-conversation',title=marker,status='active',due_at='2026-10-02T12:00:00+02:00',data={'source_references':[reference],'origin_user_prompt':True,'end_at':'2026-10-02T13:00:00+02:00'})
        manual=Item(user_id=user.id,kind='event',area='personal',source='manual',title='Evento manual',status='done',due_at='2026-10-02T09:00:00+02:00')
        uv=Item(user_id=user.id,kind='event',area='learning',source='manual',title='Sesión original',status='active',due_at='2026-10-02T10:00:00+02:00',data={'end_at':'2026-10-02T11:00:00+02:00','session_type':'class','location':'Sala de ejemplo'})
        db.add_all([derived,manual,uv]);db.commit();ids=[manual.id,uv.id];derived_id=derived.id;source_id=source.id;user_id=user.id
    urls=['/api/v1/calendar/range?start=2026-10-02&end=2026-10-03','/api/v1/calendar/day?day=2026-10-02']
    for url in urls:
        result=auth.get(url);assert result.status_code==200,result.text
        assert derived_id in {event['id'] for event in result.json()['events']}
    with SessionLocal() as db:
        if change=='revoked':db.get(Connector,('gmail',user_id)).status='revoked'
        else:db.get(Item,source_id).description+=' Changed without version increment'
        db.commit()
    for url in urls:
        result=auth.get(url);assert result.status_code==200,result.text
        assert marker not in result.text
        assert set(ids)<={event['id'] for event in result.json()['events']}
        uv_event=next(event for event in result.json()['events'] if event['id']==ids[1]);assert uv_event['category']=='class' and uv_event['location']=='Sala de ejemplo'
    with SessionLocal() as db:
        assert all(db.get(Item,id).version==1 for id in ids)
        assert db.get(Item,derived_id) is not None # Revocation hides rather than erases history.
