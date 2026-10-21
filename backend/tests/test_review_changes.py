from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from test_api import client,auth
from test_agent_runtime import enabled,owner,scheduled
from app.agent_schedules import enqueue_due_reviews,review_fingerprint
from app.database import SessionLocal,Item,AgentJob,AgentSchedule,User,Connector


def complete_and_due():
    with SessionLocal() as db:
        for job in db.scalars(select(AgentJob)):job.status='completed'
        for schedule in db.scalars(select(AgentSchedule)):schedule.next_run='2020-01-01T00:00:00+00:00'
        db.commit()


def test_unchanged_review_checks_locally_without_new_model_job(enabled):
    with SessionLocal() as db:scheduled(db,owner(db));db.commit()
    assert enqueue_due_reviews()==1
    complete_and_due()
    assert enqueue_due_reviews()==0
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AgentJob))))==1
        u=owner(db);db.add(Item(user_id=u.id,kind='task',area='personal',title='New actual task'));db.commit()
        for s in db.scalars(select(AgentSchedule)):s.next_run='2020-01-01T00:00:00+00:00'
        db.commit()
    assert enqueue_due_reviews()==1


def test_review_detects_deadline_crossing_and_madrid_day(enabled):
    with SessionLocal() as db:
        u=owner(db);at=datetime(2026,10,3,12,tzinfo=timezone.utc)
        item=Item(user_id=u.id,kind='task',area='personal',title='Deadline',due_at=(at+timedelta(minutes=61)).isoformat());db.add(item);db.commit()
        before=review_fingerprint(db,u,'personal',at)
        assert before!=review_fingerprint(db,u,'personal',at+timedelta(minutes=2))
        assert before!=review_fingerprint(db,u,'personal',at+timedelta(days=1))
        item.status='done';db.commit()
        assert review_fingerprint(db,u,'personal',at)==review_fingerprint(db,u,'personal',at+timedelta(minutes=2))


def test_other_owner_and_other_area_do_not_wake_review(enabled):
    with SessionLocal() as db:
        u=owner(db);at=datetime.now(timezone.utc);before=review_fingerprint(db,u,'personal',at)
        other=User(email='other-review@example.test',password_hash='unused');db.add(other);db.flush()
        db.add_all([Item(user_id=other.id,kind='task',area='personal',title='Other owner'),Item(user_id=u.id,kind='task',area='prisma',title='Other area')]);db.commit()
        assert review_fingerprint(db,u,'personal',at)==before
        db.get(Connector,('gmail',u.id)).status='connected';db.commit()
        assert review_fingerprint(db,u,'personal',at)!=before


def test_failed_review_does_not_suppress_next_due_attempt(enabled):
    with SessionLocal() as db:scheduled(db,owner(db));db.commit()
    assert enqueue_due_reviews()==1
    with SessionLocal() as db:
        job=db.scalar(select(AgentJob));job.status='failed'
        row=db.scalar(select(AgentSchedule));row.next_run='2020-01-01T00:00:00+00:00';db.commit()
    assert enqueue_due_reviews()==1
