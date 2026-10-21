"""Review output persistence, no provider calls and no external effects."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.database import AgentJob, Item, SessionLocal, User, Inbox, Audit
from app.conversation_engine import reference
from app.review_publish import publish_daily_review
from app.agent_capabilities import CapabilityBlocked
from test_api import client, auth
from test_conversation_engine import enabled, resource


def review(db,user,area='personal'):
    job=AgentJob(user_id=user.id,agent_id=area,prompt='Revisión periódica',is_review=True)
    db.add(job);db.flush();return job


def test_review_is_one_draft_per_area_day_with_idempotent_verified_effect(enabled):
    source=resource('personal');other=resource('events')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));original=db.get(Item,source)
        job=review(db,user)
        first=publish_daily_review(db,user,job,'La tarea merece revisión y este es el resumen interno del área.',[reference(original)])
        assert first['version']==1 and first['effect_hash']
        assert publish_daily_review(db,user,job,'La tarea merece revisión y este es el resumen interno del área.',[reference(original)])['id']==first['id']
        next_job=review(db,user)
        revised=publish_daily_review(db,user,next_job,'La segunda revisión conserva fuentes y actualiza el borrador del día.',[reference(original)])
        assert revised['id']==first['id'] and revised['version']==2
        events=publish_daily_review(db,user,review(db,user,'events'),'La revisión Eventos mantiene todos los diseños sin aprobar.',[reference(db.get(Item,other))])
        assert events['id']!=first['id']
        assert db.get(Item,first['id']).data['review_state']=='draft'
        assert not list(db.scalars(select(Inbox).where(Inbox.item_id.in_([first['id'],events['id']]))))
        db.commit();ident=first['id']
    assert enabled.get('/api/v1/items/'+ident).status_code==200
    with SessionLocal() as db:db.get(Item,source).data={'revoked':True};db.commit()
    assert enabled.get('/api/v1/items/'+ident).status_code==404


def test_review_revalidates_snapshot_and_does_not_publish_empty_or_self_context(enabled):
    source=resource('personal')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Item,source);refs=[reference(row)]
        job=review(db,user)
        assert publish_daily_review(db,user,job,'Sin fuentes disponibles para esta revisión.',[]) is None
        assert publish_daily_review(db,user,job,'OK',refs) is None
        row.description='Changed during inference';db.flush()
        with pytest.raises(CapabilityBlocked):publish_daily_review(db,user,job,'No se debe guardar esta respuesta que usó una versión anterior.',refs)


def test_review_rolls_back_and_respects_explicit_deletion(enabled):
    source=resource('personal')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Item,source)
        proof=publish_daily_review(db,user,review(db,user),'Este borrador no debe sobrevivir al rollback de la ejecución.',[reference(row)])
        ident=proof['id'];db.rollback()
    with SessionLocal() as db:
        assert db.get(Item,ident) is None
        user=db.scalar(select(User).where(User.email=='aros@local'));row=db.get(Item,source)
        db.add(Audit(user_id=user.id,action='item.deleted',subject=ident));db.flush()
        assert publish_daily_review(db,user,review(db,user),'Este borrador no debe recrearse después de borrarlo.',[reference(row)]) is None


@pytest.mark.parametrize('agent',['personal','aros'])
def test_completed_review_publishes_visible_verified_draft_through_worker(enabled,monkeypatch,agent):
    from app import openclaw
    from app.database import AgentSchedule
    source=resource('personal')
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='aros@local'));thread=str(uuid4())
        db.add(AgentSchedule(user_id=user.id,agent_id=agent,enabled=True,interval_minutes=720,thread_id=thread,next_run='2099-01-01T00:00:00+00:00'))
        job=AgentJob(user_id=user.id,agent_id=agent,thread_id=thread,prompt='Revisa los recursos actuales y prepara el resumen interno.',is_review=True)
        db.add(job);db.commit();ident=job.id
    calls=[]
    def complete(user,prompt,records,agent_id,**kwargs):
        calls.append([record.id for record in records])
        assert not kwargs.get('history')
        return 'Revisión de las fuentes actuales: consultar los recursos guardados y comprobar las tareas pendientes.',[]
    monkeypatch.setattr(openclaw,'completion',complete);openclaw.process_jobs(review_only=True,limit=1)
    assert len(calls)==1 and calls[0]
    job=enabled.get('/api/v1/agents/runs/'+ident).json()
    assert job['status']=='completed' and job['execution']['capability']=='review.digest'
    artifact=job['execution']['artifacts'][0]['id']
    assert artifact in {result['id'] for result in enabled.get('/api/v1/workspace/summary').json()['results']}
    row=enabled.get('/api/v1/items/'+artifact).json()
    assert row['area']=='personal' and row['source']=='agent-review' and row['data']['review_state']=='draft'
    assert {ref['id'] for ref in row['data']['source_references']}==set(calls[0])
