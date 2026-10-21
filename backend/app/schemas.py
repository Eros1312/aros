from datetime import datetime, timezone
from typing import Literal, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
Area = Literal['learning','events','technology','resources','server','personal']
Kind = Literal['task','event','document','email','order','link','news','note','project','service','reminder','memory','contact','expense']
Status = Literal['pending','active','done','archived','in_transit','delivered','cancelled']
Priority = Literal['normal','high','urgent']
class LoginIn(BaseModel):
    email: str = Field(min_length=1,max_length=200)
    password: str = Field(min_length=1,max_length=200)
class ItemIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Kind
    area: Area
    title: str = Field(min_length=1,max_length=240)
    description: str = Field(default='',max_length=150000)
    status: Status = 'pending'
    priority: Priority = 'normal'
    due_at: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    @field_validator('title')
    @classmethod
    def meaningful_title(cls, v):
        if not v.strip(): raise ValueError('El título no puede estar vacío')
        return v.strip()
    @field_validator('due_at')
    @classmethod
    def valid_date(cls,v):
        if v is None: return v
        d = datetime.fromisoformat(v.replace('Z','+00:00'))
        if d.tzinfo is None: raise ValueError('La fecha necesita zona horaria')
        return d.astimezone(timezone.utc).isoformat(timespec='microseconds')
    @field_validator('data')
    @classmethod
    def valid_metadata(cls,v):
        import json
        if len(json.dumps(v)) > 16000: raise ValueError('Demasiados metadatos')
        for field in ['url','tracking_url']:
            if field in v and (not isinstance(v[field],str) or not v[field].startswith(('https://','http://'))):
                raise ValueError('Los enlaces deben usar http o https')
        return v
class ItemPatch(ItemIn):
    version: int = Field(ge=1)
class ItemOut(ItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source: str
    is_demo: bool
    version: int
    created_at: str
    updated_at: str
class InboxPatch(BaseModel):
    state: Literal['new','seen','snoozed','resolved','archived']
    snoozed_until: datetime | None = None
class ConfirmationIn(BaseModel):
    action: Literal['item.delete','event.create','event.update','chat.clear','connector.disconnect','service.restart','backup.run']
    payload: dict[str, Any]
class ChatIn(BaseModel):
    text: str = Field(min_length=1,max_length=4000)
    integrated: bool = False
    idempotency_key: UUID | None = None
    authorize_internal_write: bool = True
    area: Area | Literal['all'] = 'all'
    item_ids: list[str] = Field(default_factory=list,max_length=20)
    project_id: str | None = Field(default=None,min_length=1,max_length=36)
    thread_id: str = Field(default='chat',min_length=1,max_length=36)
class AutomationIn(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    kind: Literal['daily_digest','connector_sync','server_health','deadline_review','canva_sync','github_releases']
    enabled: bool = False
    interval_minutes: int = Field(default=360,ge=5,le=10080)
class ConsentIn(BaseModel):
    ai_consent: bool
