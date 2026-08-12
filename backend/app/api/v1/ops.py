"""Protected ops endpoints: failed inbox, retry and runtime (system_admin).

整改计划 §2.2: ``GET /v1/ops/inbox?status=failed``,
``POST /v1/ops/inbox/{id}/retry`` (requires ``Idempotency-Key``) and
``GET /v1/ops/runtime``. All three are restricted to ``system_admin``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.core.errors import ValidationError
from app.core.time import utc_now
from app.models.effect import EffectLedgerEntry
from app.models.messaging import InboxEvent, InboxStatus
from app.models.reconciliation import ReconciliationRun
from app.models.runtime import RuntimeHeartbeat
from app.schemas.base import IDEMPOTENCY_KEY_HEADER
from app.services.rbac import DOMAIN_READ_ROLES
from app.services.workflows import retry_inbox_event

router = APIRouter(prefix="/v1/ops", tags=["ops"])

_SYSTEM_ADMIN = require_roles(*DOMAIN_READ_ROLES["ops"])


def _inbox_row(event: InboxEvent) -> dict[str, Any]:
    return {
        "eventId": str(event.id),
        "consumer": event.consumer,
        "status": event.status.value,
        "attempts": event.attempts,
        "nextAttemptAt": event.next_attempt_at.isoformat() if event.next_attempt_at else None,
        "leaseUntil": event.lease_until.isoformat() if event.lease_until else None,
        "lastError": event.last_error,
        "processedAt": event.processed_at.isoformat() if event.processed_at else None,
        "receivedAt": event.received_at.isoformat(),
    }


@router.get("/inbox")
def list_inbox(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(_SYSTEM_ADMIN)],
    status: str = "failed",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List inbox events by status (default ``failed``) for ops triage."""
    try:
        status_enum = InboxStatus(status)
    except ValueError as exc:
        raise ValidationError(f"unknown inbox status: {status}") from exc
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(InboxEvent).where(InboxEvent.status == status_enum)
    count_stmt = (
        select(func.count()).select_from(InboxEvent).where(InboxEvent.status == status_enum)
    )
    total = db.execute(count_stmt).scalar_one()
    events = (
        db.execute(
            stmt.order_by(InboxEvent.received_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return {
        "items": [_inbox_row(event) for event in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/inbox/{event_id}/retry")
def retry_inbox(
    event_id: uuid.UUID,
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(_SYSTEM_ADMIN)],
) -> dict[str, Any]:
    """Reset a failed inbox event to ``pending`` (Idempotency-Key required)."""
    return retry_inbox_event(
        db,
        event_id=event_id,
        idempotency_key=idempotency_key,
        actor_user_id=user_id,
    )


def _worker_snapshot(db: Session) -> dict[str, Any]:
    row = (
        db.execute(
            select(RuntimeHeartbeat)
            .where(RuntimeHeartbeat.process_name == "worker")
            .order_by(RuntimeHeartbeat.heartbeat_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return {"status": "down", "message": "no worker heartbeat recorded"}
    age = max(0, int((utc_now() - row.heartbeat_at).total_seconds()))
    return {
        "status": "ok" if age <= 30 else "warn",
        "processName": row.process_name,
        "instanceId": row.instance_id,
        "statusDetail": row.status,
        "startedAt": row.started_at.isoformat(),
        "heartbeatAt": row.heartbeat_at.isoformat(),
        "ageSeconds": age,
    }


def _inbox_snapshot(db: Session) -> dict[str, Any]:
    rows = (
        db.execute(
            select(InboxEvent.status, func.count())
            .group_by(InboxEvent.status)
        )
        .all()
    )
    counts = {status.value: count for status, count in rows}
    oldest = db.execute(
        select(func.min(InboxEvent.received_at)).where(
            InboxEvent.status.in_((InboxStatus.PENDING, InboxStatus.PROCESSING))
        )
    ).scalar_one_or_none()
    oldest_age = int((utc_now() - oldest).total_seconds()) if oldest else 0
    failed = counts.get("failed", 0)
    return {
        "status": "ok" if failed == 0 and oldest_age <= 120 else "warn",
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "processed": counts.get("processed", 0),
        "failed": failed,
        "oldestAgeSeconds": oldest_age,
    }


def _effect_snapshot(db: Session) -> dict[str, Any]:
    rows = (
        db.execute(
            select(EffectLedgerEntry.status, func.count()).group_by(EffectLedgerEntry.status)
        )
        .all()
    )
    counts = {status.value: count for status, count in rows}
    alerting = counts.get("outcome_unknown", 0) + counts.get("failed", 0)
    return {
        "status": "ok" if alerting == 0 else "warn",
        **counts,
    }


def _reconciliation_snapshot(db: Session) -> dict[str, Any]:
    run = (
        db.execute(
            select(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(1)
        )
        .scalars()
        .first()
    )
    if run is None:
        return {
            "status": "none",
            "checked": 0,
            "diffs": 0,
            "failedDomains": [],
            "skippedDomains": [],
        }
    summary = run.summary or {}
    by_domain = summary.get("by_domain") or summary.get("byDomain") or {}
    failed = list(summary.get("failedDomains") or [])
    skipped = list(summary.get("skippedDomains") or [])
    if not failed:
        failed = [
            domain
            for domain, detail in by_domain.items()
            if isinstance(detail, dict) and detail.get("status") == "failed"
        ]
    if not skipped:
        skipped = [
            domain
            for domain, detail in by_domain.items()
            if isinstance(detail, dict) and detail.get("status") == "skipped"
        ]
    return {
        "status": "ok" if not failed and not skipped else "warn",
        "runId": str(run.id),
        "runStatus": run.status.value,
        "checked": summary.get("checked", 0),
        "diffs": summary.get("diffs", 0),
        "failedDomains": failed,
        "skippedDomains": skipped,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/runtime")
def runtime(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(_SYSTEM_ADMIN)],
) -> dict[str, Any]:
    """Return worker/inbox/effect/reconciliation runtime snapshots."""
    return {
        "worker": _worker_snapshot(db),
        "inbox": _inbox_snapshot(db),
        "effect": _effect_snapshot(db),
        "reconciliation": _reconciliation_snapshot(db),
    }
