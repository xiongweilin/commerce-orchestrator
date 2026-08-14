import csv
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db, init_db
from .models import (
    Analysis,
    AnalysisJob,
    DemoSession,
    IssueCluster,
    SOPCandidate,
    SOPReview,
    Ticket,
    TicketReview,
    WeeklyReport,
)
from .schemas import ReviewPatch, TicketInput
from .service import (
    count_session_tickets_today,
    create_or_reuse_demo_session,
    enqueue_ticket,
    require_active_session,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Customer Feedback Analysis API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Demo-Client", "X-Demo-Session"],
)

REQUESTS = Counter("feedback_api_requests_total", "API requests", ["route", "status"])
LIVE_TICKETS = Counter("feedback_live_tickets_total", "Live demo tickets created")
REQUEST_SECONDS = Histogram("feedback_api_request_seconds", "API request latency", ["route"])
TICKETS_GAUGE = Gauge("feedback_tickets", "Persisted tickets")
CLUSTERS_GAUGE = Gauge("feedback_clusters", "Current issue clusters")
SOPS_GAUGE = Gauge("feedback_sop_candidates", "Current SOP candidates")
REVIEW_GAUGE = Gauge("feedback_needs_review", "Analyses waiting for review")


def _metric_route(path: str) -> str:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) >= 3 and segments[0] == "v1":
        return f"/{segments[0]}/{segments[1]}/*"
    return path


@app.middleware("http")
async def record_request(request: Request, call_next):
    route = _metric_route(request.url.path)
    with REQUEST_SECONDS.labels(route=route).time():
        response = await call_next(request)
    REQUESTS.labels(route=route, status=str(response.status_code)).inc()
    return response


def _session_or_401(db: Session, session_id: str | None) -> DemoSession:
    if not session_id:
        raise HTTPException(status_code=401, detail="missing_demo_session")
    try:
        return require_active_session(db, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _visible_ticket_or_404(
    db: Session, ticket_id: str, session_id: str | None
) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    if ticket.session_id is not None:
        _session_or_401(db, session_id)
        if ticket.session_id != session_id:
            raise HTTPException(status_code=404, detail="ticket_not_found")
    return ticket


def _enforce_quota(db: Session, settings: Settings, session_id: str) -> None:
    if count_session_tickets_today(db, session_id) >= settings.live_session_daily_limit:
        raise HTTPException(status_code=429, detail="session_daily_limit_reached")
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    demo_session = db.get(DemoSession, session_id)
    if demo_session and demo_session.ip_hash:
        ip_count = db.scalar(
            select(func.count())
            .select_from(Ticket)
            .join(DemoSession, DemoSession.id == Ticket.session_id)
            .where(
                DemoSession.ip_hash == demo_session.ip_hash,
                Ticket.ingested_at >= start,
                Ticket.source == "live",
            )
        ) or 0
        if ip_count >= settings.live_ip_daily_limit:
            raise HTTPException(status_code=429, detail="client_daily_limit_reached")
    global_count = db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.ingested_at >= start,
            Ticket.source == "live",
        )
    ) or 0
    if global_count >= settings.live_global_daily_limit:
        raise HTTPException(status_code=429, detail="global_daily_limit_reached")


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "service": "feedback-api"}


@app.get("/metrics")
def metrics(db: Annotated[Session, Depends(get_db)]) -> Response:
    TICKETS_GAUGE.set(db.scalar(select(func.count()).select_from(Ticket)) or 0)
    CLUSTERS_GAUGE.set(db.scalar(select(func.count()).select_from(IssueCluster)) or 0)
    SOPS_GAUGE.set(db.scalar(select(func.count()).select_from(SOPCandidate)) or 0)
    REVIEW_GAUGE.set(
        db.scalar(
            select(func.count()).select_from(Analysis).where(
                Analysis.review_status == "needs_review"
            )
        )
        or 0
    )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/demo/sessions", status_code=201)
def new_demo_session(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_demo_session: Annotated[str | None, Header()] = None,
    x_demo_client: Annotated[str | None, Header()] = None,
) -> dict:
    ip = x_demo_client or (request.client.host if request.client else "")
    demo_session = create_or_reuse_demo_session(
        db, settings, x_demo_session, hashlib.sha256(ip.encode()).hexdigest()
    )
    return {
        "session_id": demo_session.id,
        "expires_at": demo_session.expires_at,
        "reused": demo_session.id == x_demo_session,
    }


@app.post("/v1/tickets", status_code=202)
def create_ticket(
    payload: TicketInput,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_demo_session: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict:
    _session_or_401(db, x_demo_session)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="missing_idempotency_key")
    if len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="idempotency_key_too_long")
    existing = db.scalar(
        select(Ticket).where(
            Ticket.session_id == x_demo_session,
            or_(
                Ticket.external_id == payload.ticket_id,
                Ticket.idempotency_key == idempotency_key,
            ),
        )
    )
    if existing:
        job = db.scalar(select(AnalysisJob).where(AnalysisJob.ticket_id == existing.id))
        return {"ticket_id": existing.id, "job_id": job.id if job else None, "reused": True}
    _enforce_quota(db, settings, x_demo_session)
    try:
        ticket, job = enqueue_ticket(
            db, settings, payload, x_demo_session, idempotency_key=idempotency_key
        )
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Ticket).where(
                Ticket.session_id == x_demo_session,
                or_(
                    Ticket.external_id == payload.ticket_id,
                    Ticket.idempotency_key == idempotency_key,
                ),
            )
        )
        if existing is None:
            raise
        existing_job = db.scalar(
            select(AnalysisJob).where(AnalysisJob.ticket_id == existing.id)
        )
        return {
            "ticket_id": existing.id,
            "job_id": existing_job.id if existing_job else None,
            "reused": True,
        }
    LIVE_TICKETS.inc()
    return {"ticket_id": ticket.id, "job_id": job.id, "reused": False}


@app.post("/v1/imports", status_code=202)
async def import_tickets(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> dict:
    _session_or_401(db, x_demo_session)
    content = await file.read(settings.max_csv_bytes + 1)
    if len(content) > settings.max_csv_bytes:
        raise HTTPException(status_code=413, detail="csv_too_large")
    try:
        decoded = content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(decoded)))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=400, detail="invalid_utf8_csv") from exc
    if not rows or len(rows) > settings.max_csv_rows:
        raise HTTPException(status_code=400, detail="csv_row_limit")
    required = {"ticket_id", "message", "created_at"}
    if not required.issubset(rows[0]):
        raise HTTPException(status_code=400, detail="csv_missing_required_columns")
    results = []
    for row in rows:
        payload = TicketInput(
            ticket_id=row["ticket_id"],
            user_type=row.get("user_type") or "member",
            channel=row.get("channel") or "csv",
            message=row["message"],
            created_at=row["created_at"],
            current_status=row.get("current_status") or "open",
        )
        existing = db.scalar(
            select(Ticket).where(
                Ticket.session_id == x_demo_session,
                Ticket.external_id == payload.ticket_id,
            )
        )
        if existing:
            continue
        _enforce_quota(db, settings, x_demo_session)
        ticket, job = enqueue_ticket(
            db,
            settings,
            payload,
            x_demo_session,
            idempotency_key=f"csv:{payload.ticket_id}",
        )
        results.append({"ticket_id": ticket.id, "job_id": job.id})
        LIVE_TICKETS.inc()
    return {"accepted": len(results), "jobs": results}


@app.get("/v1/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> dict:
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    _visible_ticket_or_404(db, job.ticket_id, x_demo_session)
    analysis = db.scalar(select(Analysis).where(Analysis.ticket_id == job.ticket_id))
    return {
        "job_id": job.id,
        "ticket_id": job.ticket_id,
        "status": job.status,
        "attempts": job.attempts,
        "error": job.last_error,
        "analysis": analysis.payload if analysis else None,
    }


@app.get("/v1/tickets")
def list_tickets(
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
    limit: int = 100,
) -> list[dict]:
    limit = max(1, min(limit, 250))
    condition = Ticket.session_id.is_(None)
    if x_demo_session:
        _session_or_401(db, x_demo_session)
        condition = condition | (Ticket.session_id == x_demo_session)
    rows = db.execute(
        select(Ticket, Analysis)
        .outerjoin(Analysis, Analysis.ticket_id == Ticket.id)
        .where(condition)
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": ticket.id,
            "ticket_id": ticket.external_id,
            "user_type": ticket.user_type,
            "channel": ticket.channel,
            "message": ticket.message,
            "created_at": ticket.created_at,
            "analysis": analysis.payload if analysis else None,
        }
        for ticket, analysis in rows
    ]


@app.get("/v1/tickets/{ticket_id}")
def ticket_detail(
    ticket_id: str,
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> dict:
    ticket = _visible_ticket_or_404(db, ticket_id, x_demo_session)
    analysis = db.scalar(select(Analysis).where(Analysis.ticket_id == ticket.id))
    return {
        "id": ticket.id,
        "ticket_id": ticket.external_id,
        "message": ticket.message,
        "created_at": ticket.created_at,
        "analysis": analysis.payload if analysis else None,
    }


@app.get("/v1/clusters")
def list_clusters(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    clusters = db.scalars(
        select(IssueCluster).order_by(IssueCluster.member_count.desc())
    ).all()
    return [
        {
            "id": cluster.id,
            "title": cluster.title,
            "summary": cluster.summary,
            "member_count": cluster.member_count,
            "severity": cluster.severity,
            "trend": cluster.trend,
            "suggested_owner": cluster.suggested_owner,
            "representative_ticket_ids": cluster.representative_ticket_ids,
            "evidence": cluster.evidence,
            "narrative_source": cluster.narrative_source,
            "narrative_workflow_version": cluster.narrative_workflow_version,
        }
        for cluster in clusters
    ]


@app.get("/v1/clusters/{cluster_id}")
def cluster_detail(cluster_id: str, db: Annotated[Session, Depends(get_db)]) -> dict:
    cluster = db.get(IssueCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="cluster_not_found")
    return {
        "id": cluster.id,
        "title": cluster.title,
        "summary": cluster.summary,
        "member_count": cluster.member_count,
        "severity": cluster.severity,
        "trend": cluster.trend,
        "suggested_owner": cluster.suggested_owner,
        "representative_ticket_ids": cluster.representative_ticket_ids,
        "evidence": cluster.evidence,
        "narrative_source": cluster.narrative_source,
        "narrative_workflow_version": cluster.narrative_workflow_version,
    }


@app.get("/v1/sop-candidates")
def list_sop_candidates(
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> list[dict]:
    candidates = db.scalars(select(SOPCandidate).order_by(SOPCandidate.created_at.desc())).all()
    reviews = {}
    if x_demo_session:
        reviews = {
            review.candidate_id: review.status
            for review in db.scalars(
                select(SOPReview).where(SOPReview.session_id == x_demo_session)
            ).all()
        }
    return [
        {
            "id": candidate.id,
            "cluster_id": candidate.cluster_id,
            **candidate.payload,
            "generation_source": candidate.generation_source,
            "workflow_version": candidate.workflow_version,
            "session_status": reviews.get(candidate.id, candidate.status),
        }
        for candidate in candidates
    ]


@app.patch("/v1/sop-candidates/{candidate_id}")
def review_sop_candidate(
    candidate_id: str,
    payload: dict,
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> dict:
    _session_or_401(db, x_demo_session)
    if db.get(SOPCandidate, candidate_id) is None:
        raise HTTPException(status_code=404, detail="candidate_not_found")
    status = payload.get("status")
    if status not in {"accepted", "rejected", "pending_review"}:
        raise HTTPException(status_code=400, detail="invalid_review_status")
    review = db.scalar(
        select(SOPReview).where(
            SOPReview.session_id == x_demo_session,
            SOPReview.candidate_id == candidate_id,
        )
    )
    if review is None:
        review = SOPReview(
            session_id=x_demo_session,
            candidate_id=candidate_id,
            status=status,
            note=str(payload.get("note") or "")[:500],
        )
        db.add(review)
    else:
        review.status = status
        review.note = str(payload.get("note") or "")[:500]
    db.commit()
    return {"id": review.id, "status": review.status, "scope": "session_only"}


@app.get("/v1/reports/weekly")
def latest_weekly_report(db: Annotated[Session, Depends(get_db)]) -> dict:
    report = db.scalar(select(WeeklyReport).order_by(WeeklyReport.week_start.desc()).limit(1))
    if report is None:
        raise HTTPException(status_code=404, detail="weekly_report_not_found")
    return {
        "week_start": report.week_start,
        "payload": report.payload,
        "markdown": report.markdown,
        "generation_source": report.generation_source,
        "workflow_version": report.workflow_version,
    }


@app.get("/v1/evaluation")
def evaluation() -> dict:
    path = Path("artifacts/evaluation/evaluation.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail="evaluation_not_found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/v1/evaluation/candidate")
def candidate_evaluation() -> dict:
    directory = Path("artifacts/evaluation-v2-candidate")
    evaluation_path = directory / "evaluation.json"
    status_path = directory / "status.json"
    path = evaluation_path if evaluation_path.exists() else status_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="candidate_evaluation_not_found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/v1/evaluation/suite")
def suite_evaluation() -> dict:
    structure_path = Path("artifacts/evaluation-v7-candidate/evaluation.json")
    promotion_path = Path("artifacts/evaluation-v7-candidate/promotion-record.json")
    content_path = Path("artifacts/workflow-suite-v1-candidate/evaluation.json")
    if (
        not structure_path.exists()
        or not promotion_path.exists()
        or not content_path.exists()
    ):
        raise HTTPException(status_code=404, detail="suite_evaluation_not_found")
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
    content = json.loads(content_path.read_text(encoding="utf-8"))
    overall_passed = bool(
        structure.get("quality_gates", {}).get("all_measured_passed")
        and content.get("quality_gates", {}).get("all_passed")
    )
    return {
        "evaluation_state": promotion.get("decision"),
        "dataset_version": structure.get("dataset_version"),
        "boundary": promotion.get("scope"),
        "overall_passed": overall_passed,
        "promotion": promotion,
        "structure_clustering": structure,
        "content_workflows": content,
    }


@app.patch("/v1/tickets/{ticket_id}/review")
def review_ticket(
    ticket_id: str,
    patch: ReviewPatch,
    db: Annotated[Session, Depends(get_db)],
    x_demo_session: Annotated[str | None, Header()] = None,
) -> dict:
    _session_or_401(db, x_demo_session)
    _visible_ticket_or_404(db, ticket_id, x_demo_session)
    review = db.scalar(
        select(TicketReview).where(
            TicketReview.session_id == x_demo_session,
            TicketReview.ticket_id == ticket_id,
        )
    )
    corrections = patch.model_dump(mode="json", exclude={"status", "note"}, exclude_none=True)
    if review is None:
        review = TicketReview(
            session_id=x_demo_session,
            ticket_id=ticket_id,
            status=patch.status.value,
            corrections=corrections,
            note=patch.note,
        )
        db.add(review)
    else:
        review.status = patch.status.value
        review.corrections = corrections
        review.note = patch.note
        review.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(review)
    return {"id": review.id, "status": review.status, "corrections": review.corrections}


@app.exception_handler(Exception)
async def unhandled_exception(_request: Request, exc: Exception) -> JSONResponse:
    REQUESTS.labels(route="unhandled", status="500").inc()
    return JSONResponse(
        status_code=500,
        content={"detail": "internal_error", "type": type(exc).__name__},
    )
