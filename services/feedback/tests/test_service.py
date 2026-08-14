from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from feedback_app.analyzers import AnalyzerError
from feedback_app.config import Settings
from feedback_app.database import Base
from feedback_app.models import AnalysisJob, DemoSession, Ticket
from feedback_app.schemas import TicketInput
from feedback_app.service import (
    cleanup_expired_sessions,
    create_demo_session,
    create_or_reuse_demo_session,
    enqueue_ticket,
    process_job,
    recover_stale_jobs,
)


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ticket_job_can_be_processed_with_transparent_demo_fallback() -> None:
    db = make_db()
    settings = Settings(
        database_url="sqlite://",
        allow_demo_analyzer=True,
        dify_feedback_workflow_api_key="",
    )
    demo_session = create_demo_session(db, settings)
    ticket, job = enqueue_ticket(
        db,
        settings,
        TicketInput(
            ticket_id="T001",
            message="我们团队无法收到任务到期通知，已经联系两次。",
            created_at=datetime.now(UTC),
        ),
        demo_session.id,
    )
    analysis = process_job(db, settings, job)
    assert analysis.ticket_id == ticket.id
    assert analysis.analysis_source == "demo_rules"
    assert db.get(AnalysisJob, job.id).status == "completed"


def test_active_demo_session_is_reused_instead_of_rotating_quota_identity() -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    first = create_demo_session(db, settings, "ip")
    reused = create_or_reuse_demo_session(db, settings, first.id, "ip")
    assert reused.id == first.id
    assert db.query(DemoSession).count() == 1


def test_identical_exact_text_reuses_analysis_without_offset_risk() -> None:
    db = make_db()
    settings = Settings(
        database_url="sqlite://",
        allow_demo_analyzer=True,
        dify_feedback_workflow_api_key="",
    )
    demo_session = create_demo_session(db, settings)
    message = "我们整个项目组都收不到到期提醒，已经联系两次。"
    first, first_job = enqueue_ticket(
        db,
        settings,
        TicketInput(ticket_id="T001", message=message, created_at=datetime.now(UTC)),
        demo_session.id,
    )
    first_analysis = process_job(db, settings, first_job)
    second, second_job = enqueue_ticket(
        db,
        settings,
        TicketInput(ticket_id="T002", message=message, created_at=datetime.now(UTC)),
        demo_session.id,
    )
    second_analysis = process_job(db, settings, second_job)
    assert first.input_hash == second.input_hash
    assert first_analysis.analysis_source == "demo_rules"
    assert second_analysis.analysis_source == "cache:demo_rules"
    span = second_analysis.payload["evidence_spans"][0]
    assert second.message[span["start"] : span["end"]] == span["quote"]


def test_failed_live_analysis_is_scheduled_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://", allow_demo_analyzer=False)
    demo_session = create_demo_session(db, settings)
    _ticket, job = enqueue_ticket(
        db,
        settings,
        TicketInput(ticket_id="T003", message="通知失败", created_at=datetime.now(UTC)),
        demo_session.id,
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("feedback_app.service.DifyAnalyzer.analyze", fail)
    with pytest.raises(AnalyzerError):
        process_job(db, settings, job)
    db.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.available_at > job.updated_at


def test_expired_session_cleanup_removes_private_artifacts() -> None:
    db = make_db()
    settings = Settings(
        database_url="sqlite://",
        allow_demo_analyzer=True,
        dify_feedback_workflow_api_key="",
    )
    demo_session = create_demo_session(db, settings)
    ticket, job = enqueue_ticket(
        db,
        settings,
        TicketInput(ticket_id="T004", message="导入字段错误", created_at=datetime.now(UTC)),
        demo_session.id,
    )
    process_job(db, settings, job)
    session_id = demo_session.id
    ticket_id = ticket.id
    demo_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert cleanup_expired_sessions(db) == 1
    assert db.get(DemoSession, session_id) is None
    assert db.get(Ticket, ticket_id) is None


def test_stale_processing_job_is_requeued_after_worker_restart() -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    demo_session = create_demo_session(db, settings)
    _ticket, job = enqueue_ticket(
        db,
        settings,
        TicketInput(ticket_id="T005", message="任务卡住", created_at=datetime.now(UTC)),
        demo_session.id,
    )
    job.status = "processing"
    job.updated_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    assert recover_stale_jobs(db) == 1
    db.refresh(job)
    assert job.status == "queued"
    assert job.last_error == "recovered_stale_processing_job"
