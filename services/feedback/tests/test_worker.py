from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from feedback_app import worker
from feedback_app.config import Settings
from feedback_app.database import Base
from feedback_app.models import AnalysisJob, Ticket


def make_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_job(session_factory, *, available_at: datetime | None = None) -> str:
    with session_factory() as db:
        ticket = Ticket(
            external_id="T-WORKER",
            message="worker should process this ticket",
            input_hash="worker-hash",
            created_at=datetime.now(UTC),
        )
        db.add(ticket)
        db.flush()
        job = AnalysisJob(
            ticket_id=ticket.id,
            status="queued",
            available_at=available_at or datetime.now(UTC),
        )
        db.add(job)
        db.commit()
        return job.id


def test_run_once_returns_false_when_no_job(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = make_session_factory()
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(database_url="sqlite://"))

    assert worker.run_once() is False


def test_run_once_processes_oldest_available_job(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = make_session_factory()
    job_id = add_job(session_factory)
    calls: list[str] = []

    def process_job(_db, _settings, job):
        calls.append(job.id)

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(database_url="sqlite://"))
    monkeypatch.setattr(worker, "process_job", process_job)

    assert worker.run_once() is True
    assert calls == [job_id]


def test_run_once_ignores_future_job(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = make_session_factory()
    add_job(session_factory, available_at=datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(database_url="sqlite://"))

    assert worker.run_once() is False


def test_run_once_logs_job_failure_and_keeps_loop_alive(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    session_factory = make_session_factory()
    job_id = add_job(session_factory)

    def fail(_db, _settings, _job):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(database_url="sqlite://"))
    monkeypatch.setattr(worker, "process_job", fail)

    assert worker.run_once() is True
    assert job_id in caplog.text


def test_main_runs_periodic_cleanup_before_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    session_factory = make_session_factory()
    calls: list[str] = []
    monotonic_values = iter([61.0, 61.5])

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "init_db", lambda: calls.append("init"))
    monkeypatch.setattr(
        worker,
        "cleanup_expired_sessions",
        lambda _db: calls.append("cleanup") or 2,
    )
    monkeypatch.setattr(worker, "recover_stale_jobs", lambda _db: calls.append("recover") or 1)
    monkeypatch.setattr(worker, "run_once", lambda: False)
    monkeypatch.setattr(worker.time, "monotonic", lambda: next(monotonic_values))

    def stop_after_first_sleep(_seconds):
        calls.append("sleep")
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop_after_first_sleep)

    with pytest.raises(KeyboardInterrupt):
        worker.main()

    assert calls == ["init", "cleanup", "recover", "sleep"]
