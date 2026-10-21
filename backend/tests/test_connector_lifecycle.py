"""Synchronization cancellation, crash recovery and bounded Gmail continuation."""
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from test_api import client,auth
from app import channels,connectors
from app.config import settings
from app.database import Connector,Item,SessionLocal,User

@pytest.fixture(autouse=True)
def no_gateway(monkeypatch):
    monkeypatch.setattr(channels,'rpc',lambda method,params:{'hash':'offline-test'})

def owner(db):return db.scalar(select(User).where(User.email==settings.admin_email))

@pytest.mark.parametrize('desired',['paused','disconnected'])
@pytest.mark.parametrize('failure',[False,True])
def test_concurrent_pause_disconnect_discards_import_and_preserves_state(auth,monkeypatch,desired,failure):
    confirmation=auth.post('/api/v1/confirmations',json={'action':'connector.disconnect','payload':{'id':'rss'}}).json()['token']
    with SessionLocal() as db:
        user=owner(db);row=db.get(Connector,('rss',user.id));row.status='connected';db.commit()
        def remote(db,user):
            if desired=='paused':response=auth.patch('/api/v1/connectors/rss',json={'status':'paused'})
            else:response=auth.delete('/api/v1/connectors/rss',headers={'X-Confirmation':confirmation})
            assert response.status_code==200,response.text
            if failure:raise ValueError('provider failed after privacy change')
            connectors.upsert(db,user,'rss','discarded','news','technology','Discarded result')
            return {'created':1}
        monkeypatch.setattr(connectors,'rss_sync',remote)
        with pytest.raises(HTTPException) as error:connectors.sync_connector(db,user,'rss')
        assert error.value.status_code==(502 if failure else 409)
        db.expire_all()
        assert db.get(Connector,('rss',user.id)).status==desired
        assert not db.scalar(select(Item.id).where(Item.external_id=='discarded'))

def test_same_connector_cannot_run_twice(auth,monkeypatch):
    def remote(db,user):
        with SessionLocal() as competing:
            with pytest.raises(HTTPException) as error:connectors.sync_connector(competing,owner(competing),'rss')
            assert error.value.status_code==409
        return {'created':0}
    monkeypatch.setattr(connectors,'rss_sync',remote)
    with SessionLocal() as db:
        result=connectors.sync_connector(db,owner(db),'rss')
        assert result=={'created':0}

def test_only_expired_or_legacy_leases_are_recovered(auth):
    with SessionLocal() as db:
        user=owner(db)
        for key,age,status in [('rss',16,'syncing'),('server',1,'syncing'),('weather',None,'syncing'),('github',20,'paused')]:
            row=db.get(Connector,(key,user.id));row.status=status;row.revision=7
            row.sync_started_at=(datetime.now(timezone.utc)-timedelta(minutes=age)).isoformat() if age else None
        db.commit()
        assert connectors.recover_connector_leases(db)==2
        db.expire_all()
        assert db.get(Connector,('rss',user.id)).status=='error'
        assert db.get(Connector,('weather',user.id)).revision==8
        assert db.get(Connector,('server',user.id)).status=='syncing'
        assert db.get(Connector,('github',user.id)).status=='paused'

def test_weather_location_change_discards_old_forecast(auth,monkeypatch):
    from app import weather
    old={'label':'Old city','latitude':1,'longitude':2}
    new={'label':'New city','latitude':3,'longitude':4}
    def forecast(db,user,commit=True):
        assert commit is False
        with SessionLocal() as changed:
            row=changed.get(Connector,('weather',user.id));row.sync_details={'location':new};row.status='configured';row.revision+=1;row.sync_started_at=None;changed.commit()
        connectors.upsert(db,user,'weather','current','news','personal','Old forecast')
        return {'temperature':10}
    monkeypatch.setattr(weather,'refresh',forecast)
    with SessionLocal() as db:
        user=owner(db);row=db.get(Connector,('weather',user.id));row.sync_details={'location':old};row.status='configured';db.commit()
        with pytest.raises(HTTPException) as error:connectors.sync_connector(db,user,'weather')
        assert error.value.status_code==409
        db.expire_all();row=db.get(Connector,('weather',user.id))
        assert row.sync_details=={'location':new} and row.status=='configured'
        assert not db.scalar(select(Item.id).where(Item.source=='weather'))

def test_gmail_resumes_sixth_page_with_same_window_and_watermark(auth,monkeypatch):
    requests=[]
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Gmail:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params):
            if url.endswith('/messages'):
                requests.append(dict(params));page=int(params.get('pageToken','0'))
                data={'messages':[{'id':str(page*100+i)} for i in range(100)]}
                if page<5:data['nextPageToken']=str(page+1)
                return Response(data)
            return Response({'snippet':'Imported','payload':{'headers':[{'name':'Subject','value':'Test email'}]}})
    monkeypatch.setattr(connectors.httpx,'Client',Gmail)
    monkeypatch.setattr(connectors,'access_token',lambda db,row:'offline-token')
    with SessionLocal() as db:
        user=owner(db);row=db.get(Connector,('gmail',user.id));row.encrypted_tokens='opaque-test';row.status='connected';row.last_sync='2026-01-01T00:00:00+00:00';db.commit()
        first=connectors.sync_connector(db,user,'gmail')
        assert first['partial'] and first['gmail_window']['cursor']=='5'
        db.refresh(row);assert row.last_sync=='2026-01-01T00:00:00+00:00'
        second=connectors.sync_connector(db,user,'gmail')
        assert not second['partial'] and second['gmail_window'] is None
        assert requests[5]['pageToken']=='5'
        assert len({request['q'] for request in requests})==1
        db.refresh(row);assert row.last_sync==first['watermark']==second['watermark']
        assert db.scalar(select(func.count()).select_from(Item).where(Item.source=='gmail',Item.user_id==user.id))==600

def test_token_refresh_is_not_published_after_pause(auth,monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    class TokenResponse:
        def raise_for_status(self):pass
        def json(self):return {'access_token':'new-token','expires_in':3600}
    class TokenClient:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):
            assert auth.patch('/api/v1/connectors/gmail',json={'status':'paused'}).status_code==200
            return TokenResponse()
    monkeypatch.setattr(connectors.httpx,'Client',TokenClient)
    def importing(db,user,row):
        assert connectors.access_token(db,row)=='new-token'
        connectors.upsert(db,user,'gmail','discarded-token-refresh','email','personal','Discarded')
        return {'created':1,'partial':False,'watermark':'2026-01-01T00:00:00+00:00'}
    monkeypatch.setattr(connectors,'google_sync',importing)
    with SessionLocal() as db:
        user=owner(db);row=db.get(Connector,('gmail',user.id));row.status='connected'
        original=connectors.encode_tokens({'access_token':'old-token','refresh_token':'refresh','expires_at':'2000-01-01T00:00:00+00:00'})
        row.encrypted_tokens=original;db.commit()
        with pytest.raises(HTTPException) as error:connectors.sync_connector(db,user,'gmail')
        assert error.value.status_code==409
        db.expire_all();row=db.get(Connector,('gmail',user.id))
        assert row.status=='paused' and row.encrypted_tokens==original
        assert not db.scalar(select(Item.id).where(Item.external_id=='discarded-token-refresh'))
