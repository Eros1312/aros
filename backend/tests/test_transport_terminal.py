import json
import httpx
import pytest
from app.agent_transport import request_text


@pytest.mark.parametrize('mode',['sse','buffered','nonstream'])
@pytest.mark.parametrize('reason',['length','stop'])
def test_terminal_reason_controls_acceptance_even_with_complete_json(mode,reason):
    # A complete typed JSON object alone does not prove the provider finished.
    content=json.dumps({'kind':'tool','tool':'items.create','arguments':{'title':'Must not accept truncation'}})
    def handle(request):
        if mode!='sse':
            return httpx.Response(200,json={'choices':[{'message':{'content':content},'finish_reason':reason}]})
        chunks=[{'choices':[{'delta':{'content':content}}]}, {'choices':[{'delta':{},'finish_reason':reason}]}]
        body=''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        progress=None if mode=='nonstream' else lambda *args:None
        if reason=='length':
            with pytest.raises(ValueError,match='Truncated reply'):
                request_text(client,'https://offline.test/completions',{}, {},progress)
        else:
            assert request_text(client,'https://offline.test/completions',{}, {},progress)==content
