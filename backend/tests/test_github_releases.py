"""Offline release fixtures; actual public observations are separate receipts."""
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from sqlalchemy import select
from app import github_releases as radar
from app.connectors import upsert
from app.database import Item,Inbox,Connector
from test_printing_generation import db,user


def release(repo,ident=1):
 return {'id':ident,'tag_name':'v'+str(ident),'name':'Version','html_url':'https://github.com/'+repo+'/releases/tag/v'+str(ident),'published_at':'2026-10-03T00:00:00Z','draft':False,'prerelease':False}


def transport(monkeypatch,handle):
 original=httpx.Client
 monkeypatch.setattr(radar.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))


def unlock(db,row):
 row.sync_details={**row.sync_details,'next_allowed_at':'2000-01-01T00:00:00+00:00'};db.commit()


def test_upsert_unchanged_keeps_reference_version_and_timestamp(db,user):
 upsert(db,user,'rss','one','news','technology','Title','Text',data={'url':'https://example.org'});db.commit();item=db.scalar(select(Item));version,at=item.version,item.updated_at
 assert upsert(db,user,'rss','one','news','technology','Title','Text',data={'url':'https://example.org'}) is False
 db.commit();assert (item.version,item.updated_at)==(version,at)
 upsert(db,user,'rss','one','news','technology','Changed','Text',data={'url':'https://example.org'});db.commit();assert item.version==version+1


def test_baseline_then_unique_inbox_and_conditional_noop(db,user,monkeypatch):
 current={'id':1};calls=[]
 def handle(request):
  calls.append(request);repo='/'.join(request.url.path.split('/')[2:4]);assert 'authorization' not in request.headers
  return httpx.Response(200,json=release(repo,current['id']),headers={'etag':'"version"'})
 transport(monkeypatch,handle);row,schedule=radar.ensure_github_releases(db,user,enabled=True);db.commit()
 first=radar.refresh_github_releases(db,user);assert first['created']==5 and first['notifications']==0 and not list(db.scalars(select(Inbox)))
 before=len(calls);assert radar.refresh_github_releases(db,user)['outcome']=='cached' and len(calls)==before
 unlock(db,row);current['id']=2;second=radar.refresh_github_releases(db,user);assert second['notifications']==5
 assert calls[-1].headers['if-none-match']=='"version"'
 versions={i.id:i.version for i in db.scalars(select(Item))};unlock(db,row);radar.refresh_github_releases(db,user)
 assert len(list(db.scalars(select(Inbox))))==5 and {i.id:i.version for i in db.scalars(select(Item))}==versions


def test_no_release_and_rate_limit_are_honest(db,user,monkeypatch):
 transport(monkeypatch,lambda r:httpx.Response(404))
 row,_=radar.ensure_github_releases(db,user,enabled=True);db.commit()
 result=radar.refresh_github_releases(db,user);assert result['outcome']=='no_changes' and all(s['state']=='no_public_release' for s in result['sources'])
 unlock(db,row);original=httpx.Client
 # Existing client factory is replaced through the retained original class.
 monkeypatch.undo();transport(monkeypatch,lambda r:httpx.Response(429,headers={'Retry-After':'86400'}))
 result=radar.refresh_github_releases(db,user);assert result['outcome']=='error' and result['sources'][0]['error_code']=='rate_limited'
 assert all(s['state']=='deferred' for s in result['sources'][1:])


def test_pause_during_read_rolls_back_and_stops_next_request(db,user,monkeypatch):
 calls=[]
 def handle(request):
  calls.append(request);radar.configure_radar(radar.RadarIn(enabled=False),user,db)
  return httpx.Response(200,json=release('openclaw/openclaw'))
 transport(monkeypatch,handle);radar.ensure_github_releases(db,user,enabled=True);db.commit()
 with pytest.raises(Exception):radar.refresh_github_releases(db,user)
 assert len(calls)==1 and not list(db.scalars(select(Item)))
 assert db.get(Connector,(radar.CONTROL,user.id)).status=='paused'


def test_queue_dedup_pause_and_disconnect(db,user):
 one=radar.queue_refresh(user,db);two=radar.queue_refresh(user,db);assert one['run_id']==two['run_id']
 radar.configure_radar(radar.RadarIn(enabled=False),user,db)
 row,schedule=radar.ensure_github_releases(db,user,enabled=True);assert not schedule.enabled
 radar.disconnect_radar(user,db)
 with pytest.raises(Exception):radar.queue_refresh(user,db)
