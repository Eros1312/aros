"""Weather for an explicitly selected city. No GPS or arbitrary outbound hosts."""
from typing import Annotated
from datetime import datetime,timezone,timedelta
import httpx
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from .database import Connector,get_db,now
from .security import current_user,audit
router=APIRouter(prefix='/api/v1/weather',tags=['Weather'])
UserDep=Annotated[object,Depends(current_user)]
DbDep=Annotated[object,Depends(get_db)]
class LocationIn(BaseModel):
    label:str=Field(min_length=1,max_length=120)
    latitude:float=Field(ge=-90,le=90,allow_inf_nan=False)
    longitude:float=Field(ge=-180,le=180,allow_inf_nan=False)
def row(db,user):
    c=db.get(Connector,('weather',user.id))
    if not c:c=Connector(id='weather',user_id=user.id,name='Tiempo · Open-Meteo',scopes=[]);db.add(c);db.flush()
    return c
def describe(code):
    if code==0:return 'Despejado'
    if code in [1,2]:return 'Parcialmente nublado'
    if code==3:return 'Nublado'
    if code in [45,48]:return 'Niebla'
    if code in [51,53,55,56,57]:return 'Llovizna'
    if code in [61,63,65,66,67,80,81,82]:return 'Lluvia'
    if code in [71,73,75,77,85,86]:return 'Nieve'
    if code in [95,96,99]:return 'Tormenta'
    return 'Condiciones disponibles'
def refresh(db,user,commit=True):
    if commit:
        from .connectors import sync_connector
        return sync_connector(db,user,'weather')
    c=row(db,user);location=c.sync_details.get('location')
    if not location:raise HTTPException(409,'Elige una ciudad para conectar el tiempo')
    try:
        with httpx.Client(timeout=12,follow_redirects=False) as client:
            response=client.get('https://api.open-meteo.com/v1/forecast',params={'latitude':location['latitude'],'longitude':location['longitude'],'current':'temperature_2m,apparent_temperature,weather_code,wind_speed_10m','daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max','timezone':'auto','forecast_days':1});response.raise_for_status();data=response.json()
        current=data['current'];daily=data['daily']
        forecast={'temperature':current['temperature_2m'],'feels_like':current['apparent_temperature'],'description':describe(current['weather_code']),'code':current['weather_code'],'wind_kmh':current['wind_speed_10m'],'maximum':daily['temperature_2m_max'][0],'minimum':daily['temperature_2m_min'][0],'rain_probability':daily['precipitation_probability_max'][0],'observed_at':current['time'],'timezone':data['timezone'],'provider':'Open-Meteo','attribution':'https://open-meteo.com/'}
        from .connectors import upsert
        upsert(db,user,'weather','current','news','personal','Tiempo y clima · '+location['label'],f"{forecast['temperature']} °C · {forecast['description']}. Máxima {forecast['maximum']} °C; mínima {forecast['minimum']} °C; lluvia {forecast['rain_probability']}%. Consulta: {now()}. Fuente: Open-Meteo; previsión estimada.",data={'provider':'Open-Meteo','url':'https://open-meteo.com/','queried_at':now()})
        return forecast
    except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError):
        raise HTTPException(502,'No se pudo actualizar el tiempo. Se conserva la última lectura disponible.') from None
@router.get('/cities')
def cities(user:UserDep,q:str=Query(default='',max_length=120)):
    if len(q.strip())<2:raise HTTPException(422,'Escribe al menos dos letras de la ciudad')
    try:
        with httpx.Client(timeout=12,follow_redirects=False) as client:
            response=client.get('https://geocoding-api.open-meteo.com/v1/search',params={'name':q,'count':5,'language':'es','format':'json'});response.raise_for_status();data=response.json()
        return [{'label':', '.join(str(v) for v in [r.get('name'),r.get('admin1'),r.get('country')] if v),'latitude':r['latitude'],'longitude':r['longitude']} for r in data.get('results',[])]
    except (httpx.HTTPError,ValueError,KeyError,TypeError):raise HTTPException(502,'No se pudieron buscar ciudades') from None
@router.post('/location')
def choose(payload:LocationIn,user:UserDep,db:DbDep):
    c=row(db,user);c.sync_details={'location':payload.model_dump()};c.status='configured';c.last_sync=None;c.revision+=1;c.sync_started_at=None
    audit(db,user,'weather.location_selected','city');db.commit();refresh(db,user)
    return status(user,db)
@router.get('')
def status(user:UserDep,db:DbDep):
    c=row(db,user);data=c.sync_details;stale=True
    if c.last_sync:stale=datetime.fromisoformat(c.last_sync)<datetime.now(timezone.utc)-timedelta(minutes=30)
    if c.status in ['error','paused']:stale=True
    return {'configured':bool(data.get('location')),'location':data.get('location'),'forecast':data.get('forecast'),'stale':stale,'last_sync':c.last_sync,'error':c.error,'status':c.status}
