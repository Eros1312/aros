"""T10–T13: changes, durable receipts, failures and queue ownership."""
from copy import deepcopy
import hashlib,json
import httpx,pytest
from sqlalchemy import select
from app import printing
from app.database import Item,User,Run
from test_university import db,user
from test_printing import model

def report(candidates,states=('success',),cursor=3):
 return {'candidates':deepcopy(candidates),'checked':len(candidates),'cursor':cursor,'sources':[{'id':'source'+str(i),'state':state,'checked_at':'2026-10-02T00:00:00+00:00','fetched':len(candidates),'verified':len(candidates),'failed':int(state!='success'),'error':'Fuente no disponible' if state!='success' else None} for i,state in enumerate(states)]}

def test_T10_changes_idempotent_preserve_selection(db,user,monkeypatch):
 current=[model(1)];monkeypatch.setattr(printing,'discover_candidates',lambda *a:report(current))
 printing.refresh_printing(db,user,manual=True);item=db.scalar(select(Item));ident=item.id;discovery=item.data['first_discovered_at']
 item.data={**item.data,'favorite':True,'notes':'Privado','project_ids':['fiesta'],'approval_state':'approved'};item.status='archived';db.commit()
 current[0]['description']='Changed publisher description';current[0]['published_at']='2026-09-25T09:00:00Z';current.append(model(2))
 second=printing.refresh_printing(db,user,manual=True);db.expire_all();item=db.get(Item,ident)
 assert second['discovered']==1 and second['updated']==1
 assert item.status=='archived' and item.data['favorite'] and item.data['notes']=='Privado'
 assert item.data['project_ids']==['fiesta'] and item.data['approval_state']=='approved' and item.data['first_discovered_at']==discovery
 assert item.data['published_at']=='2026-09-25T09:00:00+00:00'
 version_before=item.version
 third=printing.refresh_printing(db,user,manual=True);db.expire_all()
 assert third['outcome']=='no_changes' and third['discovered']==third['updated']==0 and third['unchanged']==2
 assert len(list(db.scalars(select(Item))))==2 and len(printing.sync_history(user,db)['items'])==3
 assert db.get(Item,ident).version==version_before

def test_T12_no_changes_is_success_not_source_failure(db,user,monkeypatch):
 monkeypatch.setattr(printing,'discover_candidates',lambda *a:report([]));success=printing.refresh_printing(db,user,manual=True)
 assert success['outcome']=='no_changes' and not success['error'] and success['last_successful_sync_at']
 monkeypatch.setattr(printing,'discover_candidates',lambda *a:report([model(1)],('error',),99));failure=printing.refresh_printing(db,user,manual=True)
 assert failure['outcome']=='error' and failure['last_successful_sync_at']==success['last_successful_sync_at'] and failure['cursor']==success['cursor']
 assert failure['discovered']==0 and failure['sources'][0]['last_successful_sync_at']==success['sources'][0]['last_successful_sync_at'] and not list(db.scalars(select(Item)))

def test_partial_warning_keeps_prior_success_and_content(db,user,monkeypatch):
 monkeypatch.setattr(printing,'discover_candidates',lambda *a:report([model(1)]));success=printing.refresh_printing(db,user,manual=True)
 monkeypatch.setattr(printing,'discover_candidates',lambda *a:report([model(2)],('success','error')));partial=printing.refresh_printing(db,user,manual=True)
 assert partial['outcome']=='partial' and partial['discovered']==1 and partial['warning']
 assert partial['last_successful_sync_at']==success['last_successful_sync_at'] and partial['sources'][1]['last_successful_sync_at'] is None
 assert len(printing.recommendations(user,db)['items'])==2

def test_T13_refresh_joins_queued_or_running_job_and_preserves_cadence(db,user):
 row,automation=printing.ensure_printing(db,user);automation.interval_minutes=180;db.commit()
 one=printing.refresh(user,db);two=printing.refresh(user,db)
 assert one['run_id']==two['run_id'] and len(list(db.scalars(select(Run))))==1
 run=db.get(Run,one['run_id']);run.status='running';db.commit();assert printing.refresh(user,db)['running']
 other=User(email='separate@fixture',password_hash='fixture');db.add(other);db.commit()
 assert printing.sync_history(other,db)['items']==[] and printing.refresh(other,db)['run_id']!=one['run_id'] and automation.interval_minutes==180

def test_adapter_records_public_receipts_rechecks_old_and_never_replays_seed(monkeypatch):
 original=httpx.Client;feed=b'<rss><channel><item><title>3D printed party sign</title><link>https://learn.adafruit.com/party-sign</link></item></channel></rss>'
 catalog=json.dumps({'guides':[{'guide':{'title':'3D printed party sign','guide_url':'https://learn.adafruit.com/party-sign'}}]}).encode()
 def handle(request):
  return httpx.Response(200,content=catalog) if str(request.url)==printing.CATALOG else httpx.Response(403) if 'prusa' in str(request.url) else httpx.Response(200,content=feed)
 monkeypatch.setattr(printing.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
 monkeypatch.setattr(printing,'seed_candidates',lambda:pytest.fail('Historical seed is not source access'))
 seen=[]
 def verify(client,url):seen.append(url);return {**model(3),'source_url':url}
 monkeypatch.setattr(printing,'verify_page',verify);result=printing.discover_candidates({'https://learn.adafruit.com/party-sign'},0)
 assert seen==['https://learn.adafruit.com/party-sign'] and result['cursor']==1
 success=next(s for s in result['sources'] if s['id']=='adafruit-feed');assert success['response_sha256']==hashlib.sha256(feed).hexdigest() and success['verified']==1
 failure=next(s for s in result['sources'] if s['id']=='prusa-feed');assert failure['state']=='error' and failure['error_code']=='access_blocked' and 'last_successful_sync_at' not in failure

def test_party_priority_and_unknown_dates_imageless_model():
 assert printing.profile_score({'title':'Party QR sign table decoration'})[0]>printing.profile_score({'title':'Pipette calibration gearbox holder'})[0]
 candidate=printing.validate_candidate({**model(1),'published_at':'bad','provider_updated_at':'2026-01-01'})
 assert candidate['published_at'] is candidate['provider_updated_at'] is None
 assert printing.validate_candidate({**model(1),'image_url':None,'image_urls':[],'files_evidence':'Publisher printable files'})['image_url'] is None

def test_failure_receipts_cover_malformed_xml_and_rate_limit():
 assert printing.source_error(printing.ElementTree.ParseError('fixture'))=='format_changed'
 request=httpx.Request('GET',printing.FEEDS[0]);response=httpx.Response(429,headers={'Retry-After':'60'},request=request)
 assert printing.source_error(httpx.HTTPStatusError('fixture',request=request,response=response))=='rate_limited'

def test_unexpected_failure_persists_attempt_without_advancing_success_or_cursor(db,user,monkeypatch):
 monkeypatch.setattr(printing,'discover_candidates',lambda *a:report([model(1)]));success=printing.refresh_printing(db,user,manual=True)
 def broken(*a):raise RuntimeError('Do not expose provider credentials')
 monkeypatch.setattr(printing,'discover_candidates',broken)
 with pytest.raises(RuntimeError):printing.refresh_printing(db,user,manual=True)
 status=printing.recommendations(user,db)['status']
 assert status['outcome']=='error' and status['last_successful_sync_at']==success['last_successful_sync_at'] and status['cursor']==success['cursor']
 assert 'credentials' not in status['error'] and len(printing.sync_history(user,db)['items'])==2
 assert len(printing.recommendations(user,db)['items'])==1
