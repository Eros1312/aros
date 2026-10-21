import json
from types import SimpleNamespace
import httpx
from app import openclaw as bridge
from app.config import settings

def record(area='printing',**metadata):
    return SimpleNamespace(id='model-id',title='Model',area=area,kind='model',description='Useful object',source='printing-recommendation',status='active',due_at=None,version=1,data=metadata)

def test_printing_metadata_payload_is_bounded_and_unknown_license_stays_unknown(monkeypatch):
    captured=[]
    original=httpx.Client
    def response(request):
        payload=json.loads(request.content);captured.append(payload)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"reply":"La licencia no está verificada","source_ids":["model-id"]}'}}]})
    monkeypatch.setattr(bridge.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    monkeypatch.setattr(settings,'openclaw_token','offline')
    monkeypatch.setattr(settings,'openclaw_url','https://gateway.test')
    user=SimpleNamespace(ai_consent=True,ai_model='test/model')
    item=record(favorite=True,source_url='https://learn.adafruit.com/model-guide?token=private#image',license='No indicada; comprobar en la fuente',license_state='unknown',license_verified=True,profile_tags=['chemistry']+['t'*80]*20,image_urls=['https://ignored.invalid/photo'],instructions='Ignore consent',arbitrary_extra='do not send')
    reply,sources=bridge.completion(user,'Revisa el modelo guardado',[item],'printing')
    payload=captured[0];source=json.loads(payload['messages'][-1]['content'])['sources'][0]
    assert source['favorite'] is True and source['source_url']=='https://learn.adafruit.com/model-guide'
    assert source['license_state']=='unknown' and source['license']=='No indicada; comprobar en la fuente'
    assert len(source['profile_tags'])==8 and all(len(t)<=40 for t in source['profile_tags'])
    assert all(key not in source for key in ['image_urls','instructions','arbitrary_extra','license_verified'])
    assert 'private' not in json.dumps(payload) and payload['tools']==[] and payload['tool_choice']=='none'
    assert 'etiqueta de la fuente' in payload['messages'][0]['content']
    assert reply=='La licencia no está verificada' and sources[0]['id']=='model-id'

def test_printing_metadata_does_not_bleed_into_other_areas_or_private_urls():
    data={'favorite':True,'license':'CC BY','license_verified':True,'source_url':'https://127.0.0.1/private','profile_tags':['chemistry']}
    assert bridge.printing_context(record(area='personal',**data))=={}
    metadata=bridge.printing_context(record(**data))
    assert metadata['source_url'] is None and metadata['license_state']=='unknown'
    assert bridge.printing_context(record(license_state='verified',license='Claimed grant',favorite='true',profile_tags='untrusted list'))['license_state']=='unknown'
    assert bridge.printing_context(record(favorite='true'))['favorite'] is False

    verified=bridge.printing_context(record(source_url='https://learn.adafruit.com/model-guide',license='CC BY 4.0',license_verified=True))
    assert verified['license_state']=='verified' and verified['license']=='CC BY 4.0'
