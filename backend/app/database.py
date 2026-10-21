from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, String, Text, Integer, Boolean, JSON, LargeBinary, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

def now(): return datetime.now(timezone.utc).isoformat()
def ident(): return str(uuid4())
class Base(DeclarativeBase): pass
engine = create_engine(settings.database_url, connect_args={'check_same_thread': False} if settings.database_url.startswith('sqlite') else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    ai_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_model: Mapped[str] = mapped_column(String(240), default=lambda: settings.default_model, server_default='local')
class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[str] = mapped_column(String(40))
class Item(Base):
    __tablename__ = 'items'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    area: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(30), default='pending')
    priority: Mapped[str] = mapped_column(String(10), default='normal')
    due_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(80), default='manual')
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    __table_args__ = (Index('uq_external_item', 'user_id', 'source', 'external_id', unique=True),)
class Inbox(Base):
    __tablename__ = 'inbox_items'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey('items.id', ondelete='SET NULL'), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    reason: Mapped[str] = mapped_column(Text)
    area: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(20), default='new')
    snoozed_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
class Connector(Base):
    __tablename__ = 'connectors'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='disconnected')
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    encrypted_tokens: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_details: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    sync_started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
class OAuthState(Base):
    __tablename__ = 'oauth_states'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    connector: Mapped[str] = mapped_column(String(40))
    verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[str] = mapped_column(String(40))
class Confirmation(Base):
    __tablename__ = 'confirmations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[str] = mapped_column(String(40))
class Chat(Base):
    __tablename__ = 'ai_messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(20), default='local')
    model: Mapped[str] = mapped_column(String(240), default='local', server_default='local')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
class Automation(Base):
    __tablename__ = 'automations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    next_run: Mapped[str] = mapped_column(String(40), default=now)
class Run(Base):
    __tablename__ = 'automation_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    automation_id: Mapped[str] = mapped_column(ForeignKey('automations.id'))
    origin: Mapped[str] = mapped_column(String(20),default='scheduled',server_default='scheduled')
    status: Mapped[str] = mapped_column(String(20), default='pending')
    result: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
class Audit(Base):
    __tablename__ = 'activity_logs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    action: Mapped[str] = mapped_column(String(80))
    subject: Mapped[str] = mapped_column(String(240), default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)

def get_db():
    with SessionLocal() as db:
        try: yield db
        except Exception:
            db.rollback()
            raise

class AgentJob(Base):
    __tablename__ = 'agent_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    agent_id: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(240), default=lambda: settings.default_model, server_default='local')
    thread_id: Mapped[str] = mapped_column(String(36), default='main', server_default='main', index=True)
    actions: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    events: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    context_sources: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    command_key: Mapped[str | None] = mapped_column(String(36),nullable=True)
    execution: Mapped[dict] = mapped_column(JSON,default=dict,server_default='{}')
    __table_args__=(Index('uq_agent_execution_command','user_id','command_key',unique=True),)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    reply: Mapped[str] = mapped_column(Text, default='')
    sources: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    heartbeat_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    retry_after: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class AgentSchedule(Base):
    __tablename__='agent_schedules'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'),primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(30),primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer,default=720)
    next_run: Mapped[str] = mapped_column(String(40),default=now)
    thread_id: Mapped[str] = mapped_column(String(36))


class WorkerHeartbeat(Base):
    __tablename__='worker_heartbeats'
    name: Mapped[str] = mapped_column(String(40),primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36))
    heartbeat_at: Mapped[str] = mapped_column(String(40))
    last_cycle_at: Mapped[str | None] = mapped_column(String(40),nullable=True)
    last_error: Mapped[str] = mapped_column(Text,default='',server_default='')
