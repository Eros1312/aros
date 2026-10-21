import os, time
import psutil, httpx
from fastapi import HTTPException
from .config import settings

def server_health():
    if settings.server_agent_url:
        with httpx.Client(timeout=15) as client:
            r=client.get(settings.server_agent_url.rstrip('/')+'/health',headers={'Authorization':'Bearer '+settings.server_agent_token}); r.raise_for_status(); data=r.json()
        return {**data,'scope':'Agente VPS configurado'}
    memory=psutil.virtual_memory(); disk=psutil.disk_usage('/')
    try: uptime=int(time.time()-psutil.boot_time())
    except (OSError,psutil.Error): uptime=None
    return {'scope':'Equipo que ejecuta el backend','cpu_percent':psutil.cpu_percent(),'ram_used':memory.used,'ram_total':memory.total,'disk_used':disk.used,'disk_total':disk.total,'uptime_seconds':uptime,'services':[],'actions_enabled':False,'demo':False}

def server_action(action,service=None):
    if not settings.server_agent_url or not settings.server_agent_token: raise HTTPException(409,'Configura un agente VPS para ejecutar acciones')
    with httpx.Client(timeout=45) as client:
        r=client.post(settings.server_agent_url.rstrip('/')+'/action',headers={'Authorization':'Bearer '+settings.server_agent_token},json={'action':action,'service':service})
        if r.status_code>=400: raise HTTPException(502,'El agente no pudo completar la acción')
        return r.json()
