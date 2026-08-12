"""Approvals inbox: work items, decisions and the workflow next-step registry.

Work items are human tasks (approval / confirmation / manual step) attached to
a :class:`WorkflowRun`.  Submitting a decision enforces:

- the work item exists and is pending (not expired / already decided);
- the decision is one of ``approve|reject|confirm|cancel``;
- the submitted ``expected_workflow_version`` matches the current
  :class:`WorkflowRun` version (v2);
- the user holds one of the required roles (compliance may veto catalog /
  listing work items with ``compliance_vetoable`` payload);
- the four-eyes rule for refund / PO / inventory / accounting work items
  (the proposer may never approve their own proposal);
- the unique :class:`WorkItemDecision` per work item (one winner under
  concurrency, others get a 409-style :class:`ConflictError`).

Decision delivery (v2 only): the decision is recorded and ``workflow.decision_recorded``
is emitted into the worker inbox; the worker ``DBOS.send``s it and the workflow
applies the continuation after receiving it via ``DBOS.recv`` (durable approval).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import (
    ConflictError,
    IdempotencyConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.identity import User
from app.models.messaging import IdempotencyRecord
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemDecisionType,
    WorkItemKind,
    WorkItemStatus,
)
from app.schemas.events import ROLES
from app.services.audit import record_audit
from app.services.outbox_inbox import emit_event
from app.services.rbac import has_role

logger = get_logger("commerce.approvals")

VALID_DECISIONS = frozenset(("approve", "reject", "confirm", "cancel"))
FOUR_EYES_AREAS = frozenset(("refund", "po", "inventory", "accounting"))

DECISION_RECORDED_CONSUMER = "worker"
DECISION_IDEMPOTENCY_SCOPE_PREFIX = "work-item-decision:"

# workflow_type -> step_name -> next-step callback.
# Step callbacks have signature: (db, run, work_item, actor_user_id) -> dict.
_STEP_REGISTRY: dict[str, dict[str, Callable[..., dict]]] = {}


def register_next_step(workflow_type: str, step: str, callback: Callable[..., dict]) -> None:
    """Register the continuation invoked when a work item is approved."""
    _STEP_REGISTRY.setdefault(workflow_type, {})[step] = callback


def _step_for(db, run: WorkflowRun, item: WorkItem) -> Callable[..., dict] | None:
    step_name = (item.payload_json or {}).get("next_step") or "approve"
    return _STEP_REGISTRY.get(run.workflow_type, {}).get(step_name)


def _uuid(value: Any, *, field: str = "id") -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"invalid {field}: {value!r}") from exc


def _item_status_for(decision: str) -> WorkItemStatus:
    return {
        "approve": WorkItemStatus.APPROVED,
        "confirm": WorkItemStatus.COMPLETED,
        "reject": WorkItemStatus.REJECTED,
        "cancel": WorkItemStatus.CANCELLED,
    }[decision]


def create_work_item(
    db,
    *,
    workflow_id: uuid.UUID,
    kind: str,
    title: str,
    required_roles: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    expected_version: int | None = None,
    expires_at: datetime | None = None,
    proposed_by_user_id: uuid.UUID | str | None = None,
) -> WorkItem:
    """Create a pending human work item for a workflow run.

    ``proposed_by_user_id`` is the explicit four-eyes source (WP1 column);
    legacy callers that only put it in the payload are still honoured.
    """
    try:
        kind_enum = WorkItemKind(kind)
    except ValueError as exc:
        raise ValidationError(f"unknown work item kind: {kind}") from exc
    if required_roles:
        for role in required_roles:
            if role not in ROLES:
                raise ValidationError(f"unknown role: {role}")
    run = db.get(WorkflowRun, workflow_id)
    if run is None:
        raise NotFoundError(f"workflow run {workflow_id} not found")

    payload = payload or {}
    proposer = _uuid(proposed_by_user_id, field="proposed_by_user_id")
    if proposer is None:
        proposer = _uuid(payload.get("proposed_by_user_id"), field="proposed_by_user_id")

    item = WorkItem(
        workflow_id=run.id,
        kind=kind_enum,
        title=title,
        required_roles=list(required_roles) if required_roles else None,
        proposed_by_user_id=proposer,
        payload_json=payload,
        status=WorkItemStatus.PENDING,
        expected_version=expected_version if expected_version is not None else run.version,
        expires_at=expires_at,
    )
    db.add(item)
    db.flush()
    return item


def get_work_item(db, work_item_id: uuid.UUID) -> WorkItem:
    """Load a work item or raise :class:`NotFoundError`."""
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise NotFoundError(f"work item {work_item_id} not found")
    return item


def _lock_work_item(db, work_item_id: uuid.UUID) -> WorkItem:
    """Load a work item with ``SELECT ... FOR UPDATE`` (P7 durable approval)."""
    item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError(f"work item {work_item_id} not found")
    return item


def _lock_run(db, workflow_id: uuid.UUID) -> WorkflowRun:
    """Load a workflow run with ``SELECT ... FOR UPDATE``."""
    run = db.execute(
        select(WorkflowRun).where(WorkflowRun.id == workflow_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"workflow run {workflow_id} not found")
    return run


def _check_four_eyes(item: WorkItem, user_id: uuid.UUID) -> None:
    payload = item.payload_json or {}
    area = payload.get("four_eyes_area")
    if area not in FOUR_EYES_AREAS:
        return
    # Explicit WP1 column is authoritative; fall back to the legacy payload.
    proposer = item.proposed_by_user_id
    if proposer is None and payload.get("proposed_by_user_id"):
        proposer = _uuid(payload.get("proposed_by_user_id"), field="proposed_by_user_id")
    if proposer and uuid.UUID(str(proposer)) == user_id:
        raise PermissionDeniedError(
            f"four-eyes rule: the proposer may not approve their own {area} work item"
        )


def _authorized(db, item: WorkItem, user_id: uuid.UUID, decision: str) -> bool:
    required = item.required_roles or []
    if not required:
        return True
    if any(has_role(db, user_id, role) for role in required):
        return True
    payload = item.payload_json or {}
    return (
        decision == "reject"
        and payload.get("compliance_vetoable") is True
        and has_role(db, user_id, "compliance")
    )


def _cancel_run(db, run: WorkflowRun, reason: str | None) -> None:
    if run.status in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.CANCELLED):
        return
    run.status = WorkflowRunStatus.CANCELLED
    run.result_json = {"cancelled": True, "reason": reason}
    run.finished_at = utc_now()
    emit_event(
        db,
        event_type="workflow.cancelled",
        aggregate_type="workflow",
        aggregate_id=str(run.id),
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": str(run.id), "reason": reason},
    )


def apply_domain_continuation(
    db,
    *,
    run: WorkflowRun,
    item: WorkItem,
    user_id: uuid.UUID | str,
    decision: str,
    reason: str | None,
) -> dict[str, Any]:
    """Run the domain transition for a recorded decision (shared by engines).

    - ``approve``/``confirm``: invoke the registered next-step continuation
      (advances domain entities, creates the next work item, records planned
      effects) and mark the item approved/completed.
    - ``reject``/``cancel``: run an optional ``reject_step`` or cancel the
      owning run, and mark the item rejected/cancelled.

    Callers bump ``run.version`` when the run state changes afterwards.
    """
    actor = uuid.UUID(str(user_id))
    step_result: dict[str, Any] = {}
    if decision in ("approve", "confirm"):
        continuation = _step_for(db, run, item)
        if continuation is not None:
            step_result = continuation(db, run, item, actor) or {}
        item.status = (
            WorkItemStatus.APPROVED if decision == "approve" else WorkItemStatus.COMPLETED
        )
    elif decision == "reject":
        reject_step = (item.payload_json or {}).get("reject_step")
        if reject_step is not None:
            continuation = _STEP_REGISTRY.get(run.workflow_type, {}).get(reject_step)
            if continuation is not None:
                step_result = continuation(db, run, item, actor) or {}
        else:
            _cancel_run(db, run, reason)
        item.status = WorkItemStatus.REJECTED
    else:  # cancel
        _cancel_run(db, run, reason)
        item.status = WorkItemStatus.CANCELLED
    return step_result


def _decision_request_hash(
    *,
    work_item_id: uuid.UUID,
    decision: str,
    reason: str | None,
    expected_workflow_version: int | None,
) -> str:
    canonical = json.dumps(
        {
            "work_item_id": str(work_item_id),
            "decision": decision,
            "reason": reason,
            "expected_workflow_version": expected_workflow_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _decision_scope(work_item_id: uuid.UUID) -> str:
    return f"{DECISION_IDEMPOTENCY_SCOPE_PREFIX}{work_item_id}"


class DecisionResult(dict):
    """Result of submitting a decision on a work item.

    A dict subclass whose keys are the wire contract keys (``workItemId`` /
    ``status`` / ``workflowId`` ...) so the WP6 API layer can keep passing it
    straight into ``WorkItemDecisionResponse.model_validate``; attribute
    accessors mirror the keys for service-layer / test ergonomics.
    """

    def __init__(
        self,
        *,
        work_item_id: uuid.UUID,
        decision: str,
        status: str,
        workflow_id: uuid.UUID,
        workflow_status: str,
        result: dict[str, Any] | None = None,
        replayed: bool = False,
        decision_recorded: bool = False,
        submitted_version: int | None = None,
    ) -> None:
        super().__init__(
            workItemId=str(work_item_id),
            decision=decision,
            status=status,
            workflowId=str(workflow_id),
            workflowStatus=workflow_status,
            result=result,
            decisionRecorded=decision_recorded,
            replayed=replayed,
            submittedVersion=submitted_version,
        )

    @property
    def work_item_id(self) -> uuid.UUID:
        return uuid.UUID(self["workItemId"])

    @property
    def workflow_id(self) -> uuid.UUID:
        return uuid.UUID(self["workflowId"])

    @property
    def decision_recorded(self) -> bool:
        return bool(self["decisionRecorded"])

    @property
    def replayed(self) -> bool:
        return bool(self["replayed"])

    @property
    def workflow_status(self) -> str:
        return str(self["workflowStatus"])

    @property
    def submitted_version(self) -> int | None:
        return self["submittedVersion"]

    def as_dict(self) -> dict[str, Any]:
        """Return a plain dict copy of the wire payload."""
        return dict(self)


def _decision_replay(
    db,
    *,
    work_item_id: uuid.UUID,
    idempotency_key: str,
    request_hash: str,
) -> DecisionResult | None:
    """Return the stored result for an idempotent replay, or None."""
    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == _decision_scope(work_item_id),
            IdempotencyRecord.key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.request_hash != request_hash:
        raise IdempotencyConflictError(
            f"idempotency key {idempotency_key!r} was already used with a "
            "different decision body"
        )
    stored = existing.result_json or {}
    return DecisionResult(
        work_item_id=work_item_id,
        decision=str(stored.get("decision", "")),
        status=str(stored.get("status", "")),
        workflow_id=uuid.UUID(stored["workflowId"]),
        workflow_status=str(stored.get("workflowStatus", "")),
        result=stored.get("result"),
        replayed=True,
        decision_recorded=bool(stored.get("decisionRecorded", False)),
        submitted_version=stored.get("submittedVersion"),
    )


def submit_decision(
    db,
    *,
    work_item_id: uuid.UUID,
    user_id: uuid.UUID | str,
    decision: str,
    reason: str | None = None,
    expected_workflow_version: int | None = None,
    idempotency_key: str | None = None,
) -> DecisionResult:
    """Submit a decision on a pending work item (P7 durable approval).

    Pipeline: idempotency claim -> ``FOR UPDATE`` locks on the run and item ->
    active-user / role / compliance-veto / four-eyes / state / expiry checks ->
    compare ``expected_workflow_version`` against the current run version ->
    insert the unique :class:`WorkItemDecision`, update the item, bump the
    workflow/item versions -> write ``workflow.decision_recorded`` into the
    worker inbox (v2 DBOS runs).

    Concurrent approvals: the unique ``WorkItemDecision.work_item_id``
    constraint lets exactly one succeed; the rest raise
    :class:`ConflictError` (``state_conflict``).
    """
    user_id = uuid.UUID(str(user_id))
    item = get_work_item(db, work_item_id)

    # Plan 二.3: the deciding user must exist and be active (defense in depth;
    # the API layer already rejects inactive users in get_current_user).
    actor = db.get(User, user_id)
    if actor is None or not actor.is_active:
        raise PermissionDeniedError("user is not active")

    request_hash = _decision_request_hash(
        work_item_id=work_item_id,
        decision=decision,
        reason=reason,
        expected_workflow_version=expected_workflow_version,
    )
    if idempotency_key:
        replay = _decision_replay(
            db,
            work_item_id=work_item_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay

    # P7: lock the aggregate pair before any mutation (FOR UPDATE).
    item = _lock_work_item(db, work_item_id)
    run = _lock_run(db, item.workflow_id)

    if item.status != WorkItemStatus.PENDING:
        raise ConflictError(f"work item {work_item_id} is not pending (status={item.status.value})")
    if item.expires_at is not None and utc_now() > item.expires_at:
        item.status = WorkItemStatus.EXPIRED
        db.flush()
        raise ConflictError(f"work item {work_item_id} has expired")
    if decision not in VALID_DECISIONS:
        raise ValidationError(
            f"invalid decision {decision!r}; expected one of {sorted(VALID_DECISIONS)}"
        )
    if expected_workflow_version is None:
        raise ValidationError("expected_workflow_version is required")

    # Compare against the *current* run version (plan 二.3), not a snapshot.
    current_version = run.version
    if expected_workflow_version != current_version:
        raise VersionConflictError(
            f"expected workflow version {expected_workflow_version} does not match "
            f"current version {current_version}"
        )
    if not _authorized(db, item, user_id, decision):
        raise PermissionDeniedError("user does not hold a required role for this work item")
    _check_four_eyes(item, user_id)

    terminal = (
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.CANCELLED,
        WorkflowRunStatus.FAILED,
    )
    if run.status in terminal:
        raise ConflictError(f"workflow run {run.id} is {run.status.value}")

    submitted_version = (
        item.expected_version if item.expected_version is not None else run.version
    )
    decision_row = WorkItemDecision(
        work_item_id=item.id,
        decision=WorkItemDecisionType(decision),
        user_id=user_id,
        reason=reason,
        submitted_version=submitted_version,
    )
    try:
        with db.begin_nested():
            db.add(decision_row)
            db.flush()
    except IntegrityError:
        # Unique uq_work_item_decision_work_item_id: a concurrent approval won.
        raise ConflictError(
            f"work item {work_item_id} already has a decision; concurrent approval rejected"
        ) from None

    item.status = _item_status_for(decision)
    item.decision_json = {
        "decision": decision,
        "reason": reason,
        "user_id": str(user_id),
        "submitted_version": submitted_version,
        "decided_at": utc_now().isoformat(),
    }
    item.decided_by_user_id = user_id
    item.decided_at = utc_now()
    item.version += 1

    is_dbos = run.orchestration_engine == "dbos"
    if is_dbos:
        # v2: the workflow applies the continuation after DBOS.recv; the
        # worker relays this event via DBOS.send (durable decision message).
        emit_event(
            db,
            event_type="workflow.decision_recorded",
            aggregate_type="workflow",
            aggregate_id=str(run.id),
            correlation_id=run.correlation_id,
            producer="workflow",
            payload={
                "workflow_id": str(run.id),
                "work_item_id": str(item.id),
                "decision_id": str(decision_row.id),
                "decision": decision,
                "actor_user_id": str(user_id),
                "reason": reason,
                "submitted_version": submitted_version,
            },
            consumers=[DECISION_RECORDED_CONSUMER],
        )
        step_result: dict[str, Any] = {}
        run_workflow_status = run.status
    else:  # pragma: no cover - legacy_inline runs were removed with v1
        raise ValueError(
            f"submit_decision supports only dbos runs; got {run.orchestration_engine!r}"
        )

    result = DecisionResult(
        work_item_id=item.id,
        decision=decision,
        status=item.status.value,
        workflow_id=run.id,
        workflow_status=run_workflow_status.value,
        result=step_result or None,
        replayed=False,
        decision_recorded=is_dbos,
        submitted_version=submitted_version,
    )

    record_audit(
        db,
        actor_user_id=user_id,
        action="work_item.decision",
        resource_type="work_item",
        resource_id=str(item.id),
        changes={
            "decision": decision,
            "reason": reason,
            "workflow_id": str(run.id),
            "workflow_status": run.status.value,
            "decision_recorded": is_dbos,
        },
        correlation_id=run.correlation_id,
    )
    if idempotency_key:
        db.add(
            IdempotencyRecord(
                scope=_decision_scope(work_item_id),
                key=idempotency_key,
                request_hash=request_hash,
                status="completed",
                result_json=result.as_dict(),
            )
        )
    db.flush()
    return result


__all__ = [
    "DECISION_RECORDED_CONSUMER",
    "DECISION_IDEMPOTENCY_SCOPE_PREFIX",
    "DecisionResult",
    "FOUR_EYES_AREAS",
    "VALID_DECISIONS",
    "apply_domain_continuation",
    "create_work_item",
    "get_work_item",
    "register_next_step",
    "submit_decision",
]
