import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .analysis import finalize_analysis
from .analyzers import AnalyzerError, DemoAnalyzer, DifyAnalyzer
from .config import Settings
from .models import (
    Analysis,
    AnalysisJob,
    ClusterMember,
    DemoSession,
    SOPReview,
    Ticket,
    TicketReview,
    utc_now,
)
from .privacy import sanitize_message
from .schemas import TicketInput


def input_hash(message: str, workflow_version: str) -> str:
    # Evidence offsets are tied to the exact persisted text. Cache only byte-for-byte
    # equivalent sanitized messages so reused offsets cannot point at the wrong span.
    return hashlib.sha256(f"{workflow_version}\0{message}".encode()).hexdigest()


def create_demo_session(db: Session, settings: Settings, ip_hash: str = "") -> DemoSession:
    session = DemoSession(
        ip_hash=ip_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.demo_session_ttl_hours),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def create_or_reuse_demo_session(
    db: Session,
    settings: Settings,
    existing_session_id: str | None,
    ip_hash: str = "",
) -> DemoSession:
    if existing_session_id:
        try:
            return require_active_session(db, existing_session_id)
        except ValueError:
            pass
    return create_demo_session(db, settings, ip_hash)


def require_active_session(db: Session, session_id: str) -> DemoSession:
    demo_session = db.get(DemoSession, session_id)
    expires_at = demo_session.expires_at if demo_session else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if demo_session is None or expires_at is None or expires_at < datetime.now(UTC):
        raise ValueError("demo_session_expired")
    return demo_session


def count_session_tickets_today(db: Session, session_id: str) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.ingested_at >= start,
            Ticket.source == "live",
        )
    ) or 0


def enqueue_ticket(
    db: Session,
    settings: Settings,
    payload: TicketInput,
    session_id: str | None,
    source: str = "live",
    idempotency_key: str | None = None,
) -> tuple[Ticket, AnalysisJob]:
    sanitized = sanitize_message(payload.message)
    ticket = Ticket(
        external_id=payload.ticket_id,
        session_id=session_id,
        user_type=payload.user_type,
        channel=payload.channel,
        message=sanitized,
        created_at=payload.created_at,
        current_status=payload.current_status,
        input_hash=input_hash(sanitized, settings.workflow_version),
        idempotency_key=idempotency_key,
        source=source,
    )
    db.add(ticket)
    db.flush()
    job = AnalysisJob(ticket_id=ticket.id)
    db.add(job)
    db.commit()
    db.refresh(ticket)
    db.refresh(job)
    return ticket, job


def _try_cache_hit(db: Session, settings: Settings, ticket: Ticket) -> Analysis | None:
    """Return a cached analysis if an identical input was already processed."""
    cached = db.scalar(
        select(Analysis)
        .join(Ticket, Analysis.ticket_id == Ticket.id)
        .where(
            Ticket.input_hash == ticket.input_hash,
            Ticket.id != ticket.id,
            Analysis.workflow_version == settings.workflow_version,
        )
        .order_by(Analysis.created_at.desc())
        .limit(1)
    )
    if cached is None:
        return None
    payload = deepcopy(cached.payload)
    origin_source = cached.analysis_source
    while origin_source.startswith("cache:"):
        origin_source = origin_source.removeprefix("cache:")
    payload["analysis_source"] = f"cache:{origin_source}"[:32]
    analysis = Analysis(
        ticket_id=ticket.id,
        payload=payload,
        problem_type=cached.problem_type,
        product_area=cached.product_area,
        suggested_owner=cached.suggested_owner,
        severity=cached.severity,
        needs_escalation=cached.needs_escalation,
        review_status=cached.review_status,
        workflow_version=cached.workflow_version,
        analysis_source=payload["analysis_source"],
    )
    db.add(analysis)
    return analysis


def _perform_analysis(db: Session, settings: Settings, ticket: Ticket) -> Analysis:
    """Run the actual analyzer (Dify or demo) and persist the result."""
    ticket_payload = TicketInput(
        ticket_id=ticket.external_id,
        user_type=ticket.user_type,
        channel=ticket.channel,
        message=ticket.message,
        created_at=ticket.created_at,
        current_status=ticket.current_status,
    )
    source = "dify"
    try:
        raw = DifyAnalyzer(settings).analyze(ticket_payload)
    except Exception as exc:
        if not settings.allow_demo_analyzer:
            raise AnalyzerError(f"{type(exc).__name__}: {exc}"[:1000]) from exc
        raw = DemoAnalyzer().analyze(ticket_payload)
        source = "demo_rules"
    final = finalize_analysis(
        ticket.message,
        raw,
        settings.workflow_version,
        source,
        settings.routing_policy_version,
    )
    analysis = Analysis(
        ticket_id=ticket.id,
        payload=final.model_dump(mode="json"),
        problem_type=final.problem_type.value,
        product_area=final.product_area.value,
        suggested_owner=final.suggested_owner.value,
        severity=final.severity.value,
        needs_escalation=final.needs_escalation,
        review_status=final.review_status.value,
        workflow_version=final.workflow_version,
        analysis_source=final.analysis_source,
    )
    db.add(analysis)
    return analysis


def process_job(db: Session, settings: Settings, job: AnalysisJob) -> Analysis:  # noqa: S3776 (orchestrator with clear phases - acceptable depth)
    ticket = db.get(Ticket, job.ticket_id)
    if ticket is None:
        raise ValueError("ticket_not_found")
    existing = db.scalar(select(Analysis).where(Analysis.ticket_id == ticket.id))
    if existing:
        job.status = "completed"
        job.updated_at = utc_now()
        db.commit()
        return existing

    cached = _try_cache_hit(db, settings, ticket)
    if cached:
        job.status = "completed" if cached.review_status != "needs_review" else "needs_review"
        job.last_error = None
        job.updated_at = utc_now()
        db.commit()
        return cached

    job.status = "processing"
    job.attempts += 1
    job.updated_at = utc_now()
    db.commit()

    try:
        analysis = _perform_analysis(db, settings, ticket)
    except AnalyzerError as exc:
        job.status = "queued" if job.attempts < 3 else "failed"
        job.last_error = str(exc)[:1000]
        job.updated_at = utc_now()
        if job.status == "queued":
            delay_seconds = 2 if job.attempts == 1 else 8
            job.available_at = utc_now() + timedelta(seconds=delay_seconds)
        db.commit()
        raise
    job.status = "completed" if analysis.review_status != "needs_review" else "needs_review"
    job.last_error = None
    job.updated_at = utc_now()
    db.commit()
    db.refresh(analysis)
    return analysis


def cleanup_expired_sessions(db: Session) -> int:
    expired_ids = list(
        db.scalars(select(DemoSession.id).where(DemoSession.expires_at < utc_now())).all()
    )
    if not expired_ids:
        return 0
    ticket_ids = select(Ticket.id).where(Ticket.session_id.in_(expired_ids))
    db.execute(
        delete(TicketReview).where(
            (TicketReview.session_id.in_(expired_ids)) | (TicketReview.ticket_id.in_(ticket_ids))
        )
    )
    db.execute(delete(SOPReview).where(SOPReview.session_id.in_(expired_ids)))
    db.execute(delete(ClusterMember).where(ClusterMember.ticket_id.in_(ticket_ids)))
    db.execute(delete(Analysis).where(Analysis.ticket_id.in_(ticket_ids)))
    db.execute(delete(AnalysisJob).where(AnalysisJob.ticket_id.in_(ticket_ids)))
    db.execute(delete(Ticket).where(Ticket.session_id.in_(expired_ids)))
    db.execute(delete(DemoSession).where(DemoSession.id.in_(expired_ids)))
    db.commit()
    return len(expired_ids)


def recover_stale_jobs(db: Session, stale_after_seconds: int = 120) -> int:
    stale_before = utc_now() - timedelta(seconds=stale_after_seconds)
    result = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.status == "processing",
            AnalysisJob.updated_at < stale_before,
        )
        .values(
            status="queued",
            available_at=utc_now(),
            last_error="recovered_stale_processing_job",
            updated_at=utc_now(),
        )
    )
    db.commit()
    return result.rowcount or 0
