import json
import httpx
import pytest
from app.agent_transport import request_text,reported_usage


def test_usage_only_reports_present_valid_counters():
    assert reported_usage({}) is None
    assert reported_usage({'usage':{'prompt_tokens':True,'total_tokens':-1}}) is None
    assert reported_usage({'usage':{'prompt_tokens':12}},'reported-model')=={'prompt_tokens':12,'model':'reported-model','reported':True}
    assert reported_usage({'usage':{'total_tokens':0}})=={'total_tokens':0,'reported':True}

@pytest.mark.parametrize('stream',[True,False])
def test_usage_is_captured_without_inventing_cost_or_quota(stream):
    seen=[];calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        answer={'model':'gateway-model','usage':{'prompt_tokens':17,'completion_tokens':4,'total_tokens':21}}
        if not stream:return httpx.Response(200,json={**answer,'choices':[{'message':{'content':[{'type':'text','text':'ok'}]},'finish_reason':'stop'}]})
        chunks=[{'model':'gateway-model','choices':[{'delta':{'content':'ok'}}]}, {**answer,'choices':[]}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert request_text(client,'https://offline.test',{}, {},lambda *a:seen.append(a))=='ok'
    usage=[v for k,v in seen if k=='usage']
    assert usage==[{'prompt_tokens':17,'completion_tokens':4,'total_tokens':21,'model':'gateway-model','reported':True}]
    assert calls[0]['stream_options']=={'include_usage':True}
    assert len(calls)==1


def test_cancellation_closes_stream_before_remaining_chunks_are_read():
    closed=[];read=[];checks=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for n in range(5):
                read.append(n)
                yield ('data: '+json.dumps({'choices':[{'delta':{'content':'x'}}]})+'\n\n').encode()
        def close(self):closed.append(True)
    def progress(kind,value):
        if kind=='check':
            checks.append(True)
            if len(checks)==2:raise RuntimeError('cancelled')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream()))) as client:
        with pytest.raises(RuntimeError,match='cancelled'):
            request_text(client,'https://offline.test',{}, {},progress)
    assert read==[0]
    assert closed==[True]


def test_cancellation_before_request_makes_no_network_call():
    calls=[]
    def progress(kind,value):raise RuntimeError('cancelled')
    with httpx.Client(transport=httpx.MockTransport(lambda r:calls.append(r))) as client:
        with pytest.raises(RuntimeError,match='cancelled'):request_text(client,'https://offline.test',{}, {},progress)
    assert not calls
