import json
from datetime import datetime, timezone
from sqlalchemy import select
from fastapi import HTTPException
import pytest
from app import printing
from app.database import Automation, Connector, Item, User
from test_university import db, user


def model(n):
    return {'title':f'Printed sensor holder {n}', 'description':'Holder for electronics and organization',
            'source_url':f'https://learn.adafruit.com/printed-sensor-holder-{n}',
            'image_url':f'https://cdn-learn.adafruit.com/assets/assets/000/000/{n:03}/medium800/holder.jpg',
            'license':'No indicada; comprobar en la fuente','license_state':'unknown','license_verified':False,
            'license_url':f'https://learn.adafruit.com/printed-sensor-holder-{n}',
            'verified_at':'2026-10-02T00:00:00+00:00','source':'Adafruit Learning System',
            'usage':'Soporte para organizar componentes y montar sensores.','profile_tags':['tecnología','organización']}


def test_scheduler_is_persistent_idempotent_and_opt_in_every_twelve_hours(db,user):
    printing.ensure_printing(db,user);printing.ensure_printing(db,user);db.commit()
    automations=list(db.scalars(select(Automation)))
    assert len(automations)==1 and not automations[0].enabled and automations[0].interval_minutes==720


def test_rechecks_seen_models_idempotently_without_forcing_a_quota(db,user,monkeypatch):
    calls=[]
    def discover(excluded,cursor):calls.append((excluded,cursor));return [model(n) for n in range(25)],[]
    monkeypatch.setattr(printing,'discover_candidates',discover)
    first=printing.refresh_printing(db,user,manual=True);second=printing.refresh_printing(db,user,manual=True)
    assert first['discovered']==25 and first['outcome']=='updated'
    assert second['discovered']==0 and second['unchanged']==25 and second['outcome']=='no_changes'
    assert second['deficit']==0 and second['error'] is None
    assert len(list(db.scalars(select(Item).where(Item.source==printing.SOURCE))))==25
    assert calls[1][0]==set() # Existing pages are checked for actual changes.
    assert second['next_refresh'] is None  # A manual refresh does not opt in to recurring requests.
    feed=printing.recommendations(user,db)
    assert len(feed['items'])==25 and feed['status']['outcome']=='no_changes'


def test_owner_isolation_does_not_reuse_another_users_seen_state(db,user,monkeypatch):
    monkeypatch.setattr(printing,'discover_candidates',lambda excluded,cursor:([model(n) for n in range(10)],[]))
    printing.refresh_printing(db,user,manual=True)
    other=User(email='other-printing@fixture',password_hash='fixture');db.add(other);db.flush()
    assert printing.refresh_printing(db,other,manual=True)['discovered']==10
    assert len(printing.recommendations(other,db)['items'])==10
    assert printing.recommendations(other,db)['profile']['printer'] is None
    assert all(item.user_id==other.id for item in db.scalars(select(Item).where(Item.user_id==other.id)))


def test_provider_failure_keeps_prior_models_and_reports_actual_shortfall(db,user,monkeypatch):
    monkeypatch.setattr(printing,'discover_candidates',lambda excluded,cursor:([model(n) for n in range(10)],[]))
    printing.refresh_printing(db,user,manual=True)
    monkeypatch.setattr(printing,'discover_candidates',lambda excluded,cursor:([],['Feed no disponible']))
    failed=printing.refresh_printing(db,user,manual=True)
    assert failed['discovered']==0 and failed['outcome']=='error' and failed['provider_errors']==['Feed no disponible']
    assert failed['last_successful_sync_at']!=failed['last_attempt_at']
    assert len(printing.recommendations(user,db)['items'])==10


def test_overlapping_refresh_is_rejected(db,user):
    row,automation=printing.ensure_printing(db,user);automation.enabled=True;row.status='syncing';row.sync_started_at=datetime.now(timezone.utc).isoformat();db.commit()
    with pytest.raises(HTTPException) as error:printing.refresh_printing(db,user)
    assert error.value.status_code==409


@pytest.mark.parametrize('changes',[
    {'source_url':'http://learn.adafruit.com/test'}, {'source_url':'https://evil.example/test'},
    {'image_url':'https://127.0.0.1/internal'}, {'image_url':None}, {'license':None},
    {'verified_at':None}, {'license_state':'unknown','license':'MIT','license_verified':False},
])
def test_unverified_or_external_candidates_cannot_be_recommended(changes):
    assert printing.validate_candidate({**model(1),**changes}) is None


def test_verified_license_is_kept_separate_from_unknown_rights():
    candidate=printing.validate_candidate({**model(1),'license':'CC-BY-SA-4.0','license_verified':True,'license_state':'verified'})
    assert candidate['license']=='CC-BY-SA-4.0' and candidate['license_verified']


def test_release_has_no_unverified_third_party_catalog_seed():
    # Real recommendations are discovered and verified at the operator's request.
    # The public source release does not bundle scraped content or present fake
    # publisher records as previously verified observations.
    assert printing.seed_candidates() == []


def test_page_verification_requires_design_files_and_preserves_unknown_license(monkeypatch):
    text=b'<meta property="og:title" content="Printed sensor holder"><meta property="og:description" content="Holder for electronics"><meta property="og:image" content="https://cdn-learn.adafruit.com/assets/assets/001/holder.jpg"><a href="https://example.org/holder.stl">Download STLs</a>'
    monkeypatch.setattr(printing,'fetch_public',lambda *args,**kwargs:text)
    candidate=printing.verify_page(None,'https://learn.adafruit.com/holder')
    assert candidate and candidate['license_state']=='unknown' and not candidate['license_verified']
    monkeypatch.setattr(printing,'fetch_public',lambda *args,**kwargs:text.replace(b'holder.stl',b'homepage').replace(b'Download STLs',b'Download'))
    assert printing.verify_page(None,'https://learn.adafruit.com/holder') is None


def test_profile_matching_does_not_mistake_portalable_for_lab():
    _,tags=printing.profile_score({'title':'Portable sound box','description':'A portalable sound system'})
    assert 'química' not in tags


def test_collection_filters_paginate_and_favorites_survive_reload(db,user,monkeypatch):
    monkeypatch.setattr(printing,'discover_candidates',lambda excluded,cursor:([model(n) for n in range(10)],[]))
    printing.refresh_printing(db,user,manual=True)
    first=printing.recommendations(user,db,limit=3)
    second=printing.recommendations(user,db,limit=3,offset=first['pagination']['next_offset'])
    assert first['pagination']['total']==10 and first['status']['total']==10
    assert len(second['items'])==3 and not ({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
    assert len(printing.recommendations(user,db,category='tecnología')['items'])==10
    assert len(printing.recommendations(user,db,category='organización')['items'])==10
    item=db.get(Item,first['items'][0]['id']);item.title='Organización para Química';db.commit()
    result=printing.favorite_model(item.id,printing.FavoriteIn(favorite=True),user,db)
    assert result['favorite'] is True
    assert printing.recommendations(user,db,q='QUIMICA')['items'][0]['id']==item.id
    assert [i['id'] for i in printing.recommendations(user,db,favorite=True)['items']]==[item.id]
    printing.favorite_model(item.id,printing.FavoriteIn(favorite=False),user,db)
    assert printing.recommendations(user,db,favorite=True)['pagination']['total']==0
    assert len(printing.recommendations(user,db,q='%')['items'])==0


def test_favorites_cannot_change_another_owner_or_a_non_model(db,user,monkeypatch):
    monkeypatch.setattr(printing,'discover_candidates',lambda excluded,cursor:([model(1)],[]))
    printing.refresh_printing(db,user,manual=True)
    item_id=printing.recommendations(user,db)['items'][0]['id']
    other=User(email='other-favorite@fixture',password_hash='fixture');db.add(other);db.flush()
    with pytest.raises(HTTPException) as error:printing.favorite_model(item_id,printing.FavoriteIn(favorite=True),other,db)
    assert error.value.status_code==404
    manual=Item(user_id=user.id,kind='model',area='printing',title='Original',source='manual');db.add(manual);db.flush()
    with pytest.raises(HTTPException):printing.favorite_model(manual.id,printing.FavoriteIn(favorite=True),user,db)
    assert printing.recommendations(user,db)['items'][0]['favorite'] is False


def test_manual_refresh_queues_once_without_network_and_keeps_auto_paused(db,user,monkeypatch):
    row,automation=printing.ensure_printing(db,user);automation.enabled=False;row.status='paused';db.commit()
    monkeypatch.setattr(printing,'discover_candidates',lambda *a:pytest.fail('API must not discover synchronously'))
    first=printing.refresh(user,db);second=printing.refresh(user,db)
    assert first['queued'] and first['run_id']==second['run_id']
    assert db.get(printing.Run,first['run_id']).origin=='manual'
    feed=printing.recommendations(user,db)
    assert feed['status']['queued'] and feed['status']['enabled'] is False and feed['status']['next_refresh'] is None
    with pytest.raises(HTTPException) as error:printing.refresh_printing(db,user)
    assert error.value.status_code==409 and row.status=='paused'


def test_pausing_during_discovery_preserves_previous_collection(db,user,monkeypatch):
    _,automation=printing.ensure_printing(db,user);automation.enabled=True;db.commit()
    def discover(*args):
        automation.enabled=False;db.commit();return [model(1)],[]
    monkeypatch.setattr(printing,'discover_candidates',discover)
    with pytest.raises(HTTPException) as error:printing.refresh_printing(db,user)
    assert error.value.status_code==409
    feed=printing.recommendations(user,db)
    assert not feed['items'] and feed['status']['enabled'] is False
    assert db.get(Connector,(printing.CONTROL,user.id)).status=='paused'


def test_a_site_footer_license_is_not_the_printable_models_license(monkeypatch):
    raw=b'<meta property="og:title" content="Printed sensor holder"><meta property="og:description" content="Sensor holder"><meta property="og:image" content="https://media.printables.com/holder.jpg"><main>No model license stated</main><footer>Website text license <a href="https://creativecommons.org/licenses/by/4.0">CC</a></footer>'
    monkeypatch.setattr(printing,'fetch_public',lambda *a,**kw:raw)
    assert printing.verify_page(None,'https://www.printables.com/model/123-holder') is None


def test_model_scoped_license_preserves_source_url_and_rejects_unknown_cc_paths(monkeypatch):
    page='<meta property="og:title" content="Printed sensor holder"><meta property="og:description" content="Sensor holder"><meta property="og:image" content="https://media.printables.com/holder.jpg"><script type="application/ld+json">'+json.dumps({'@type':'3DModel','name':'Printed sensor holder','license':'https://creativecommons.org/licenses/by/4.0'})+'</script>'
    monkeypatch.setattr(printing,'fetch_public',lambda *a,**kw:page.encode())
    result=printing.verify_page(None,'https://www.printables.com/model/123-holder')
    assert result['source_url']=='https://www.printables.com/model/123-holder'
    assert result['license_verified'] and result['license']=='licenses/by/4.0'
    page=page.replace('/by/4.0','/not-a-license/99.0')
    assert printing.verify_page(None,'https://www.printables.com/model/123-holder') is None


def test_running_job_is_visible_before_the_connector_is_claimed(db,user):
    row,automation=printing.ensure_printing(db,user);db.commit()
    run=printing.Run(user_id=user.id,automation_id=automation.id,origin='manual',status='running');db.add(run);db.commit()
    assert row.status=='connected'
    feed=printing.recommendations(user,db)
    assert feed['status']['running'] and not feed['status']['queued']
