from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from feedback_app.config import Settings, get_settings
from feedback_app.database import Base, get_db
from feedback_app.main import app


def make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def make_client(db, **kw):
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_settings] = lambda: Settings(database_url="sqlite://", **kw)
    return TestClient(app)


def test_tickets_empty():
    db = make_db()
    c = make_client(db)
    r = c.get("/v1/tickets")
    assert r.status_code == 200
    app.dependency_overrides.clear()
    db.close()


def test_clusters_empty():
    db = make_db()
    c = make_client(db)
    r = c.get("/v1/clusters")
    assert r.status_code == 200
    app.dependency_overrides.clear()
    db.close()


def test_ticket_not_found():
    db = make_db()
    c = make_client(db)
    r = c.get("/v1/tickets/nonexistent")
    assert r.status_code == 404
    app.dependency_overrides.clear()
    db.close()