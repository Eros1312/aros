"""Owner-isolated in-memory database fixtures; no external service."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.config import settings
from app.database import Base,User
@pytest.fixture
def db(monkeypatch):
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings,'encryption_key',Fernet.generate_key().decode())
    with Session(engine) as session:yield session
    engine.dispose()
@pytest.fixture
def user(db):
    user=User(email='member@example.test',password_hash='fixture');db.add(user);db.flush();return user
