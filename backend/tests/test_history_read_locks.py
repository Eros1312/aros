"""PostgreSQL compilation distinguishes presentation reads from effect fences."""
import pytest
from sqlalchemy import event,select
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql
from test_api import client,auth
from test_conversation_engine import enabled,resource,complete_write
from test_agent_loop import complete as complete_autonomous
from app import conversation_engine,openclaw
from app.database import SessionLocal,User,Connector,AgentJob

@pytest.mark.parametrize('mode',['conversation','autonomous'])
def test_chat_and_job_presentations_use_current_guards_without_row_locks(enabled,monkeypatch,mode):
    source=resource(source='gmail')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        db.get(Connector,('gmail',user.id)).status='connected';db.commit()
    if mode=='conversation':ident,_=complete_write(enabled,monkeypatch,source)
    else:ident,_=complete_autonomous(monkeypatch,enabled,source)
    statements=[]
    def capture(execution):
        if execution.is_select:statements.append(str(execution.statement.compile(dialect=postgresql.dialect())))
    event.listen(Session,'do_orm_execute',capture)
    try:
        for path in ['/api/v1/chat','/api/v1/agents/runs',f'/api/v1/agents/runs/{ident}','/api/v1/agents/runtime']:
            statements.clear();result=enabled.get(path);assert result.status_code==200,result.text
            assert statements and not any('FOR UPDATE' in sql for sql in statements)
        # Explicit execution/mutation authorization still locks item and
        # connector snapshots. SQLite would silently ignore these SQL clauses.
        statements.clear()
        with SessionLocal() as db:
            job=db.get(AgentJob,ident);user=db.get(User,job.user_id)
            assert openclaw.history_is_authorized(db,user,job,lock=True)
            if mode=='conversation':conversation_engine.check(db,user,job.execution)
        locked=[sql for sql in statements if 'FOR UPDATE' in sql]
        assert any('FROM items' in sql for sql in locked)
        assert any('FROM connectors' in sql for sql in locked)
        assert all('ORDER BY items.id' in sql for sql in locked if 'FROM items' in sql)
        with SessionLocal() as db:
            user=db.scalar(select(User).where(User.email=='aros@local'));db.get(Connector,('gmail',user.id)).status='revoked';db.commit()
        statements.clear();dto=enabled.get('/api/v1/agents/runs/'+ident).json()
        assert dto['resources_unavailable'] and dto['reply']=='' and dto['actions']==[]
        assert not any('FOR UPDATE' in sql for sql in statements)
        if mode=='conversation':
            statements.clear();history=enabled.get('/api/v1/chat').json()
            assert next(message for message in history if message.get('job_id')==ident and message['role']=='assistant')['resources_unavailable']
            assert not any('FOR UPDATE' in sql for sql in statements)
    finally:event.remove(Session,'do_orm_execute',capture)
