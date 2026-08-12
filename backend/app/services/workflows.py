"""Workflow queries plus the WP4/WP5 write facades used by the API layer.

The API layer (``app.api``) calls the fixed WP4 contract signatures
``accept_command(command, actor, idempotency_key, correlation_id)`` and
``submit_decision(work_item_id, actor, decision, expected_version,
idempotency_key)`` through this module.  Until WP4 lands the real
implementations in ``app.services.commands`` / ``app.services.approvals``,
the facades delegate to the current v1 services while implementing the
Idempotency-Key semantics (replay / 409 conflict / 409 in-progress) on top.
"""

from __future__ import annotations

import uuid
from typing import Any

from prometheus_client import Counter
from sqlalchemy import func, select

from app.core.errors import IdempotencyConflictError, NotFoundError, ValidationError
from app.core.time import utc_now
from app.models.effect import EffectLedgerEntry
from app.models.messaging import IdempotencyRecord, InboxEvent, InboxStatus, OutboxEvent
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem, WorkItemStatus
from app.schemas.base import (
    IDEMPOTENCY_SCOPE_DECISION,
    IDEMPOTENCY_SCOPE_DIFF_RESOLVE,
    IDEMPOTENCY_SCOPE_INBOX_RETRY,
)
from app.schemas.commands import AcceptedCommand, DecisionResult
from app.services.approvals import submit_decision as _submit_decision
from app.services.commands import (
    IDEMPOTENCY_SCOPE_COMMAND,
    canonical_hash,
)
from app.services.commands import (
    accept_command as _accept_command_v2,
)
from app.services.reconciliation import resolve_diff as _resolve_diff

IDEMPOTENCY_RESULTS = Counter(
    "commerce_idempotency_total",
    "Idempotency outcomes observed by the API write facades",
    ["result"],
)


class IdempotencyInProgressError(IdempotencyConflictError):
    """409 with ``Retry-After: 1``: same key and body, still being processed."""

    type = "idempotency_in_progress"
    title = "Idempotency request in progress"


def _work_item_summary(item: WorkItem) -> dict[str, Any]:
    """Canonical work-item shape (计划 §四.1).

    Returns ``expectedWorkflowVersion`` as the canonical field, plus
    ``workflowId`` and ``createdAt``.
    """
    return {
        "workItemId": str(item.id),
        "workflowId": str(item.workflow_id),
        "kind": item.kind.value,
        "title": item.title,
        "status": item.status.value,
        "requiredRoles": item.required_roles or [],
        "assigneeUserId": str(item.assignee_user_id) if item.assignee_user_id else None,
        "expectedWorkflowVersion": item.expected_version,
        "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
        "decidedByUserId": str(item.decided_by_user_id) if item.decided_by_user_id else None,
        "decidedAt": item.decided_at.isoformat() if item.decided_at else None,
        "createdAt": item.created_at.isoformat(),
        "payload": item.payload_json or {},
    }


def get_workflow(db, workflow_id: uuid.UUID | str) -> dict[str, Any]:
    """Return the full workflow status view for a workflow run."""
    run = db.get(WorkflowRun, uuid.UUID(str(workflow_id)))
    if run is None:
        raise NotFoundError(f"workflow {workflow_id} not found")

    work_items = (
        db.execute(
            select(WorkItem).where(WorkItem.workflow_id == run.id).order_by(WorkItem.created_at)
        )
        .scalars()
        .all()
    )
    events = (
        db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.correlation_id == run.correlation_id)
            .order_by(OutboxEvent.occurred_at)
        )
        .scalars()
        .all()
    )
    effects = (
        db.execute(select(EffectLedgerEntry).where(EffectLedgerEntry.approval_ref == run.id))
        .scalars()
        .all()
    )

    if events:
        current_step = events[-1].event_type
    elif any(item.status.value == "pending" for item in work_items):
        current_step = "waiting_for_approval"
    else:
        current_step = run.status.value

    return {
        "workflowId": str(run.id),
        "type": run.workflow_type,
        "status": run.status.value,
        "currentStep": current_step,
        "expectedWorkflowVersion": run.version,
        "input": run.input_json,
        "result": run.result_json,
        "error": run.error,
        "events": [
            {
                "eventId": str(event.event_id),
                "type": event.event_type,
                "occurredAt": event.occurred_at.isoformat(),
            }
            for event in events
        ],
        "effects": [
            {
                "effectId": str(effect.intent_id),
                "operation": f"{effect.target_system}.{effect.operation}",
                "status": effect.status.value,
                "remoteReference": effect.remote_reference,
                "attempt": effect.attempt,
                "errorDetail": effect.error_detail,
            }
            for effect in effects
        ],
        "workItems": [_work_item_summary(item) for item in work_items],
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
    }


def get_work_item(db, work_item_id: uuid.UUID) -> WorkItem:
    from app.services.approvals import get_work_item as _get  # re-export convenience

    return _get(db, work_item_id)


def list_workflows(
    db,
    *,
    status: str | None = None,
    workflow_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of workflow runs (newest first) for the console."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(WorkflowRun)
    count_stmt = select(func.count()).select_from(WorkflowRun)
    if status:
        try:
            status_enum = WorkflowRunStatus(status)
        except ValueError as exc:
            raise ValidationError(f"unknown workflow status: {status}") from exc
        stmt = stmt.where(WorkflowRun.status == status_enum)
        count_stmt = count_stmt.where(WorkflowRun.status == status_enum)
    if workflow_type:
        stmt = stmt.where(WorkflowRun.workflow_type == workflow_type)
        count_stmt = count_stmt.where(WorkflowRun.workflow_type == workflow_type)
    total = db.execute(count_stmt).scalar_one()
    runs = (
        db.execute(stmt.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )

    # currentStep: a run with pending human work items waits for approval.
    run_ids = [run.id for run in runs]
    awaiting: set[uuid.UUID] = set()
    if run_ids:
        awaiting = set(
            db.execute(
                select(WorkItem.workflow_id).where(
                    WorkItem.workflow_id.in_(run_ids),
                    WorkItem.status == WorkItemStatus.PENDING,
                )
            )
            .scalars()
            .all()
        )

    return {
        "items": [
            {
                "workflowId": str(run.id),
                "type": run.workflow_type,
                "status": run.status.value,
                "currentStep": "waiting_for_approval" if run.id in awaiting else run.status.value,
                "correlationId": run.correlation_id,
                "createdAt": run.created_at.isoformat(),
                "updatedAt": run.updated_at.isoformat(),
            }
            for run in runs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Idempotency machinery (WP6 API semantics; WP4 takes over the service side)
# ---------------------------------------------------------------------------


def _is_processing(record: IdempotencyRecord) -> bool:
    """A record is in-flight until the transaction marks it ``completed``."""
    return record.status == "processing"


def idempotency_lookup(db, *, scope: str, key: str) -> IdempotencyRecord | None:
    """Return the stored idempotency record for ``(scope, key)``."""
    if not key:
        raise ValidationError("idempotency key is required")
    return db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()


def check_idempotency(
    db,
    *,
    scope: str,
    key: str,
    request_hash: str,
) -> IdempotencyRecord | None:
    """Enforce the plan's idempotency semantics for a write request.

    - no record            -> None (caller proceeds);
    - same hash, completed -> the stored record (caller replays the result);
    - same hash, processing-> 409 ``idempotency_in_progress`` (+ Retry-After);
    - different hash       -> 409 ``idempotency_key_conflict``.
    """
    existing = idempotency_lookup(db, scope=scope, key=key)
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        IDEMPOTENCY_RESULTS.labels(result="conflict").inc()
        raise IdempotencyConflictError(
            f"idempotency key {key!r} was already used with a different request body"
        )
    if _is_processing(existing):
        IDEMPOTENCY_RESULTS.labels(result="in_progress").inc()
        raise IdempotencyInProgressError(
            f"idempotency key {key!r} is still being processed; retry shortly"
        )
    return existing


def open_idempotency(db, *, scope: str, key: str, request_hash: str) -> IdempotencyRecord:
    """Atomically claim a write under ``(scope, key)`` in the caller's transaction."""
    record = IdempotencyRecord(
        scope=scope,
        key=key,
        request_hash=request_hash,
        status="processing",
    )
    db.add(record)
    db.flush()
    return record


def complete_idempotency(record: IdempotencyRecord, *, result: dict[str, Any]) -> None:
    """Mark a claimed idempotency record completed with its stored result."""
    record.status = "completed"
    record.result_json = result


# ---------------------------------------------------------------------------
# WP4 contract facades (fixed signatures; interim delegation to v1 services)
# ---------------------------------------------------------------------------


def accept_command(
    command: Any,
    actor: uuid.UUID | str,
    idempotency_key: str,
    correlation_id: str | None,
    *,
    db,
    command_type: str,
) -> AcceptedCommand:
    """Accept a write command (WP4 contract) with Idempotency-Key semantics.

    ``command`` is a typed Pydantic command model; ``command_type`` matches
    the command domain (``catalog-revision`` etc.).

    Delegates to WP4's ``app.services.commands.accept_command``: the command
    is accepted as a DBOS v2 workflow run (``orchestration_engine='dbos'``,
    ``workflow_version=2``) and the worker starts the v2 definition from the
    ``workflow.accepted`` inbox event.  No domain state migration and no
    external effect runs inside this transaction (整改计划 §二.1 / §四.1).
    Idempotency scope is the fixed ``command`` scope (WP4 owns the record);
    the facade keeps the in-progress / conflict pre-checks for the 409
    semantics required by the API contract.
    """
    payload = command.model_dump(mode="json") if hasattr(command, "model_dump") else dict(command)
    request_hash = canonical_hash(payload)
    existing = idempotency_lookup(db, scope=IDEMPOTENCY_SCOPE_COMMAND, key=idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            IDEMPOTENCY_RESULTS.labels(result="conflict").inc()
            raise IdempotencyConflictError(
                f"idempotency key {idempotency_key!r} was already used "
                "with a different request body"
            )
        if _is_processing(existing):
            IDEMPOTENCY_RESULTS.labels(result="in_progress").inc()
            raise IdempotencyInProgressError(
                f"idempotency key {idempotency_key!r} is still being processed; retry shortly"
            )
        stored = existing.result_json or {}
        IDEMPOTENCY_RESULTS.labels(result="replay").inc()
        return AcceptedCommand(
            workflowId=stored["workflowId"],
            status="accepted",
            statusUrl=stored.get("statusUrl", f"/v1/workflows/{stored['workflowId']}"),
            replayed=True,
        )
    accepted = _accept_command_v2(
        db,
        command={"type": command_type, "payload": payload},
        actor_user_id=uuid.UUID(str(actor)),
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    return AcceptedCommand(
        workflowId=accepted.workflow_id,
        status="accepted",
        statusUrl=accepted.status_url,
        replayed=accepted.replayed,
    )


def submit_decision(
    work_item_id: uuid.UUID,
    actor: uuid.UUID | str,
    decision: str,
    expected_version: int | None,
    idempotency_key: str | None,
    *,
    db,
    reason: str | None = None,
) -> DecisionResult:
    """Submit a work-item decision (WP4 contract) with Idempotency-Key support.

    ``idempotency_key`` is optional for backward compatibility with existing
    clients (整改计划 §四.1 asks for uniform Idempotency-Key; the current
    console decision form and the existing API tests do not send one yet).
    When present, replay / conflict / in-progress semantics apply.

    Integration note: the actual decision runs in WP4's
    ``app.services.approvals.submit_decision`` (called without its own
    idempotency key; the WP6 record below is authoritative here).  The WP4
    replay/conflict path is not exercised by this facade, so its
    ``idempotency_in_progress`` handling stays the WP6 contract.
    """
    request_hash = canonical_hash(
        {
            "work_item_id": str(work_item_id),
            "decision": decision,
            "expected_version": expected_version,
            "reason": reason,
        }
    )
    if idempotency_key:
        existing = check_idempotency(
            db,
            scope=IDEMPOTENCY_SCOPE_DECISION,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            IDEMPOTENCY_RESULTS.labels(result="replay").inc()
            return DecisionResult.model_validate({**existing.result_json, "replayed": True})
        record = open_idempotency(
            db,
            scope=IDEMPOTENCY_SCOPE_DECISION,
            key=idempotency_key,
            request_hash=request_hash,
        )
    result = _submit_decision(
        db,
        work_item_id=work_item_id,
        user_id=uuid.UUID(str(actor)),
        decision=decision,
        reason=reason,
        expected_workflow_version=expected_version,
    )
    if idempotency_key:
        complete_idempotency(record, result=result)
        db.flush()
    return DecisionResult.model_validate({**result, "replayed": False})


def resolve_diff_with_idempotency(
    db,
    *,
    run_id: uuid.UUID | str,
    diff_id: uuid.UUID | str,
    note: str,
    resolver_user_id: uuid.UUID | str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Manually resolve a reconciliation diff (WP5 contract) with idempotency.

    ``idempotency_key`` is optional for compatibility with the current console
    resolve form; when present, replay / conflict / in-progress apply.
    """
    request_hash = canonical_hash(
        {"run_id": str(run_id), "diff_id": str(diff_id), "note": note}
    )
    if idempotency_key:
        existing = check_idempotency(
            db,
            scope=IDEMPOTENCY_SCOPE_DIFF_RESOLVE,
            key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            IDEMPOTENCY_RESULTS.labels(result="replay").inc()
            return {**existing.result_json, "replayed": True}
        record = open_idempotency(
            db,
            scope=IDEMPOTENCY_SCOPE_DIFF_RESOLVE,
            key=idempotency_key,
            request_hash=request_hash,
        )
    diff = _resolve_diff(
        db,
        diff_id=diff_id,
        note=note,
        resolver_user_id=resolver_user_id,
    )
    result = {
        "diffId": str(diff.id),
        "status": diff.status.value,
        "resolvedAt": diff.resolved_at.isoformat() if diff.resolved_at else None,
    }
    if idempotency_key:
        complete_idempotency(record, result=result)
        db.flush()
    return result


def retry_inbox_event(
    db,
    *,
    event_id: uuid.UUID | str,
    idempotency_key: str,
    actor_user_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Reset a failed inbox event to ``pending`` for another worker attempt.

    Requires an ``Idempotency-Key`` (计划 §2.2: retry 必须携带).
    """
    request_hash = canonical_hash({"event_id": str(event_id)})
    existing = check_idempotency(
        db,
        scope=IDEMPOTENCY_SCOPE_INBOX_RETRY,
        key=idempotency_key,
        request_hash=request_hash,
    )
    if existing is not None:
        IDEMPOTENCY_RESULTS.labels(result="replay").inc()
        return {**existing.result_json, "replayed": True}
    record = open_idempotency(
        db,
        scope=IDEMPOTENCY_SCOPE_INBOX_RETRY,
        key=idempotency_key,
        request_hash=request_hash,
    )
    event = db.get(InboxEvent, uuid.UUID(str(event_id)))
    if event is None:
        raise NotFoundError(f"inbox event {event_id} not found")
    event.status = InboxStatus.PENDING
    event.attempts = 0
    event.next_attempt_at = None
    event.lease_until = None
    event.last_error = None
    db.flush()
    result = {
        "eventId": str(event.id),
        "status": event.status.value,
        "retriedAt": utc_now().isoformat(),
        "actorUserId": str(actor_user_id),
    }
    complete_idempotency(record, result=result)
    db.flush()
    return result


__all__ = [
    "IDEMPOTENCY_RESULTS",
    "IdempotencyInProgressError",
    "accept_command",
    "check_idempotency",
    "complete_idempotency",
    "get_work_item",
    "get_workflow",
    "idempotency_lookup",
    "list_workflows",
    "open_idempotency",
    "resolve_diff_with_idempotency",
    "retry_inbox_event",
    "submit_decision",
]
