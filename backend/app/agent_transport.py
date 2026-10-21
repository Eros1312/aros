"""OpenClaw's documented SSE transport; only public reply deltas are surfaced."""
import json,re

def reported_usage(data, model=None):
    """Keep only counters actually returned by the gateway; absence is unknown."""
    usage=data.get('usage')
    if not isinstance(usage,dict):return None
    result={}
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        value=usage.get(key)
        if type(value) is int and 0<=value<=10**12:result[key]=value
    if not result:return None
    actual=data.get('model') or model
    if isinstance(actual,str) and len(actual)<=200:result['model']=actual
    result['reported']=True
    return result

def response_text(data):
    choice=data['choices'][0]
    if choice.get('finish_reason')=='length':raise ValueError('Truncated reply')
    message=choice['message']
    if message.get('tool_calls'):raise ValueError('Unexpected external tool request')
    content=message['content']
    if isinstance(content,list):content=''.join(part.get('text','') for part in content if isinstance(part,dict) and part.get('type')=='text')
    if not isinstance(content,str) or len(content)>256000:raise ValueError('Invalid reply size')
    return content

def reply_fragment(text):
    clean=text.strip().removeprefix('```json').removeprefix('```').lstrip()
    match=re.match(r'^\{\s*"reply"\s*:\s*',clean)
    if not match:return None
    value=clean[match.end():]
    if not value.startswith('"'):return None
    try:
        result,_=json.JSONDecoder().raw_decode(value)
        return result if isinstance(result,str) else None
    except ValueError:pass
    # A chunk can end midway through an escape or Unicode codepoint.
    for trim in range(7):
        prefix=value if trim==0 else value[:-trim]
        try:return json.loads(prefix+'"')
        except ValueError:continue
    return None

def decode_reply(text,records):
    clean=text.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:structured=json.loads(clean)
    except ValueError:
        # Some gateways surround the typed envelope with explanatory prose.
        structured=None
        for match in re.finditer(r'\{',clean):
            try:candidate,_=json.JSONDecoder().raw_decode(clean[match.start():])
            except ValueError:continue
            if isinstance(candidate,dict) and isinstance(candidate.get('reply'),str):structured=candidate;break
    if not isinstance(structured,dict) or not isinstance(structured.get('reply'),str):return text,[],[]
    ids=structured.get('source_ids',[])
    if not isinstance(ids,list):ids=[]
    allowed={str(r.id):r for r in records}
    sources=[]
    for ident in ids:
        if not isinstance(ident,str) or ident not in allowed or any(s['id']==ident for s in sources):continue
        record=allowed[ident];sources.append({'id':record.id,'title':record.title,'area':record.area,'kind':record.kind})
    return structured['reply'],sources,structured.get('actions',[])

def request_text(client,url,headers,payload,progress=None):
    if progress is None:
        response=client.post(url,headers=headers,json=payload)
        response.raise_for_status()
        return response_text(response.json())
    progress('check','')
    progress('request','Solicitando una respuesta al modelo elegido')
    with client.stream('POST',url,headers=headers,json={**payload,'stream':True,'stream_options':{'include_usage':True}}) as response:
        if response.is_error:response.read()
        response.raise_for_status()
        if 'text/event-stream' not in response.headers.get('content-type',''):
            response.read();progress('buffered','El gateway devolvió una respuesta completa')
            progress('check','')
            data=response.json();usage=reported_usage(data)
            if usage:progress('usage',usage)
            return response_text(data)
        text='';done=False;last_visible='';receiving=False;model=None;last_usage=None
        for line in response.iter_lines():
            progress('check','')
            if not line.startswith('data:'):continue
            data=line[5:].strip()
            if data=='[DONE]':done=True;break
            if not data:continue
            chunk=json.loads(data)
            if chunk.get('error'):raise ValueError('Gateway stream failed')
            if isinstance(chunk.get('model'),str):model=chunk['model']
            usage=reported_usage(chunk,model)
            if usage and usage!=last_usage:
                progress('usage',usage);last_usage=usage
            for choice in chunk.get('choices',[]):
                if choice.get('finish_reason')=='length':raise ValueError('Truncated reply')
                delta=choice.get('delta',{})
                if delta.get('tool_calls'):raise ValueError('Unexpected external tool request')
                content=delta.get('content')
                if not isinstance(content,str):continue
                if not receiving:progress('receiving','Recibiendo la respuesta del modelo');receiving=True
                text+=content
                if len(text)>256000:raise ValueError('Reply exceeds limit')
                visible=reply_fragment(text)
                if visible is not None and visible!=last_visible:
                    progress('reply',visible);last_visible=visible
        if not done:raise ValueError('Interrupted stream')
        progress('check','')
        return text
