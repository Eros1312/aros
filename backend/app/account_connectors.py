"""Account imports with fixed vendor endpoints and encrypted, owner-scoped tokens."""
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,SecretStr
import httpx
from .database import Connector,get_db
from .security import current_user,audit
from .connectors import encode_tokens,decode_tokens,upsert
KEYS={'github','notion','vercel'}
router=APIRouter(prefix='/api/v1/connectors',tags=['Account connections'])
class CredentialIn(BaseModel):
    token:SecretStr

def fetch_page(client,key,token,cursor=None):
    headers={'Authorization':'Bearer '+token}
    if key=='github':
        headers.update({'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'})
        response=client.get('https://api.github.com/user/repos',headers=headers,params={'per_page':100,'page':cursor or 1,'sort':'updated'})
    elif key=='notion':
        headers['Notion-Version']='2025-09-03'
        body={'page_size':100,'filter':{'value':'page','property':'object'}}
        if cursor: body['start_cursor']=cursor
        response=client.post('https://api.notion.com/v1/search',headers=headers,json=body)
    else:
        params={'limit':100}
        if cursor:params['until']=cursor
        response=client.get('https://api.vercel.com/v9/projects',headers=headers,params=params)
    if response.status_code in [401,403]:raise HTTPException(409,'El proveedor rechazó el token o sus permisos. Revísalos en su panel.')
    if response.status_code==429:raise HTTPException(429,'El proveedor ha alcanzado su cuota. Espera antes de sincronizar.')
    response.raise_for_status();return response.json()

@router.post('/{key}/credential')
def configure(key:str,payload:CredentialIn,user:Annotated[object,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    if key not in KEYS:raise HTTPException(404,'Este conector no utiliza un token personal')
    token=payload.token.get_secret_value().strip()
    if not 10<=len(token)<=4096 or any(c.isspace() for c in token):raise HTTPException(422,'Introduce un token válido directamente en este panel')
    try:
        with httpx.Client(timeout=20,follow_redirects=False) as client:fetch_page(client,key,token)
    except HTTPException:raise
    except (httpx.HTTPError,ValueError,TypeError):raise HTTPException(502,'No se pudo validar la conexión con el proveedor') from None
    connector=db.get(Connector,(key,user.id))
    if not connector:raise HTTPException(404,'Conector no encontrado')
    connector.encrypted_tokens=encode_tokens({'access_token':token});connector.status='configured';connector.error=None;connector.revision+=1;connector.sync_started_at=None
    audit(db,user,'connector.configured',key);db.commit()
    return {'configured':True,'requires_sync':True}

def account_sync(db,user,connector):
    token=decode_tokens(connector.encrypted_tokens)['access_token'];key=connector.id
    count=0;cursor=None;partial=False
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        for page in range(4):
            data=fetch_page(client,key,token,cursor)
            records=data if key=='github' else data.get('results',[]) if key=='notion' else data.get('projects',[])
            for record in records:
                if key=='github':
                    title=record['full_name'];description=record.get('description') or '';kind='project';area='technology';url=record['html_url']
                elif key=='notion':
                    title='Página de Notion'
                    for prop in record.get('properties',{}).values():
                        if prop.get('type')=='title':title=''.join(t.get('plain_text','') for t in prop.get('title',[])) or title
                    description='Página compartida con la integración. Importa título y enlace; el contenido de bloques no se descarga.';kind='document';area='personal';url=record.get('url','')
                else:
                    title=record['name'];description='Proyecto Vercel · '+str(record.get('framework') or 'Sin framework');kind='project';area='server';url='https://vercel.com/dashboard'
                count+=upsert(db,user,key,str(record['id']),kind,area,title,description,data={'url':url})
            cursor=(page+2 if len(records)==100 else None) if key=='github' else data.get('next_cursor') if key=='notion' and data.get('has_more') else data.get('pagination',{}).get('next') if key=='vercel' else None
            if not cursor:break
            partial=True
    return {'created':count,'pages':page+1,'partial':bool(cursor),'note':'Importación de títulos, descripciones y enlaces; no realiza cambios en el proveedor.'}
