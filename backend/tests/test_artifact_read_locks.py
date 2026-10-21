"""Compile actual ORM reads as PostgreSQL SQL: SQLite alone ignores row locks."""
import pytest
from sqlalchemy import event,select
from sqlalchemy.orm import Session
from sqlalchemy.dialects import postgresql
from test_api import client,auth
from test_agent_loop import complete,resource as loop_resource
from test_agent_execution import resource,enqueue,command,step
from app import agent_loop
from app.agent_capabilities import revalidate_sources
from app.database import SessionLocal,User,Connector,AgentJob

@pytest.mark.parametrize('mode',['selection','autonomous'])
def test_artifact_get_has_no_row_locks_but_effect_guards_remain_locked(auth,monkeypatch,mode):
    assert auth.patch('/api/v1/me',json={'ai_consent':True}).status_code==200
    source=(loop_resource if mode=='autonomous' else resource)(source='gmail')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'))
        db.get(Connector,('gmail',user.id)).status='connected';db.commit()
    if mode=='autonomous':ident,_=complete(monkeypatch,auth,source)
    else:
        ident=enqueue(auth,command(source))
        for _ in range(3):step()
    path=f'/api/v1/agents/{"autonomous" if mode=="autonomous" else "executions"}/{ident}/artifact'
    statements=[]
    def capture(execution):
        if execution.is_select:statements.append(str(execution.statement.compile(dialect=postgresql.dialect())))
    event.listen(Session,'do_orm_execute',capture)
    try:
        result=auth.get(path);assert result.status_code==200,result.text
        assert statements and not any('FOR UPDATE' in sql for sql in statements)
        statements.clear()
        with SessionLocal() as db:
            job=db.get(AgentJob,ident);user=db.get(User,job.user_id)
            if mode=='autonomous':agent_loop.verified_artifact(db,user,job,job.execution)
            else:revalidate_sources(db,user,job.execution['scope'],job.execution['selected_sources'])
        locked=[sql for sql in statements if 'FOR UPDATE' in sql]
        assert any('FROM items' in sql for sql in locked)
        assert any('FROM connectors' in sql for sql in locked)
        with SessionLocal() as db:
            user=db.scalar(select(User).where(User.email=='aros@local'))
            db.get(Connector,('gmail',user.id)).status='revoked';db.commit()
        statements.clear()
        assert auth.get(path).status_code==409
        assert statements and not any('FOR UPDATE' in sql for sql in statements)
    finally:event.remove(Session,'do_orm_execute',capture)
