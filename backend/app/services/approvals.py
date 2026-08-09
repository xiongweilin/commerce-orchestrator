"""Approvals inbox: work items, decisions and the workflow next-step registry.

Work items are human tasks (approval / confirmation / manual step) attached to
a :class:`WorkflowRun`.  Submitting a decision enforces:

- the work item exists and is pending (not expired / already decided);
- the decision is one of ``approve|reject|confirm|cancel``;
- the submitted ``expected_workflow_version`` matches the item's version
  (:class:`VersionConflictError` otherwise);
- the user holds one of the required roles (compliance may veto catalog /
  listing work items with ``compliance_vetoable`` payload);
- the four-eyes rule for refund / PO / inventory / accounting work items
  (the proposer may never approve their own proposal).

On ``approve``/``confirm`` the registered next step of the owning workflow is
run synchronously in the same transaction, then the item is marked completed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.logging import get_logger
from app.core.time import utc_now
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

# workflow_type -> step_name -> next-step callback.
# Step callbacks have signature: (db, run, work_item, actor_user_id) -> dict.
_STEP_REGISTRY: dict[str, dict[str, Callable[..., dict]]] = {}


def register_next_step(workflow_type: str, step: str, callback: Callable[..., dict]) -> None:
    """Register the continuation invoked when a work item is approved."""
    _STEP_REGISTRY.setdefault(workflow_type, {})[step] = callback


def _step_for(db, run: WorkflowRun, item: WorkItem) -> Callable[..., dict] | None:
    step_name = (item.payload_json or {}).get("next_step") or "approve"
    return _STEP_REGISTRY.get(run.workflow_type, {}).get(step_name)


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
) -> WorkItem:
    """Create a pending human work item for a workflow run."""
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

    item = WorkItem(
        workflow_id=run.id,
        kind=kind_enum,
        title=title,
        required_roles=list(required_roles) if required_roles else None,
        payload_json=payload or {},
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


def _check_four_eyes(item: WorkItem, user_id: uuid.UUID) -> None:
    payload = item.payload_json or {}
    area = payload.get("four_eyes_area")
    if area not in FOUR_EYES_AREAS:
        return
    proposer = payload.get("proposed_by_user_id")
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
    emit_event(
        db,
        event_type="workflow.cancelled",
        aggregate_type="workflow",
        aggregate_id=str(run.id),
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": str(run.id), "reason": reason},
    )


def submit_decision(
    db,
    *,
    work_item_id: uuid.UUID,
    user_id: uuid.UUID | str,
    decision: str,
    reason: str | None = None,
    expected_workflow_version: int | None = None,
) -> dict[str, Any]:
    """Submit a decision on a pending work item and run the next workflow step."""
    item = get_work_item(db, work_item_id)
    user_id = uuid.UUID(str(user_id))
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
    if expected_workflow_version != item.expected_version:
        raise VersionConflictError(
            f"expected workflow version {expected_workflow_version} does not match "
            f"current version {item.expected_version}"
        )
    if not _authorized(db, item, user_id, decision):
        raise PermissionDeniedError("user does not hold a required role for this work item")
    _check_four_eyes(item, user_id)

    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise NotFoundError(f"workflow run {item.workflow_id} not found")
    terminal = (
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.CANCELLED,
        WorkflowRunStatus.FAILED,
    )
    if run.status in terminal:
        raise ConflictError(f"workflow run {run.id} is {run.status.value}")

    submitted_version = item.expected_version if item.expected_version is not None else run.version
    db.add(
        WorkItemDecision(
            work_item_id=item.id,
            decision=WorkItemDecisionType(decision),
            user_id=user_id,
            reason=reason,
            submitted_version=submitted_version,
        )
    )

    step_result: dict[str, Any] = {}
    if decision in ("approve", "confirm"):
        continuation = _step_for(db, run, item)
        if continuation is not None:
            step_result = continuation(db, run, item, user_id) or {}
        item.status = WorkItemStatus.APPROVED if decision == "approve" else WorkItemStatus.COMPLETED
    elif decision == "reject":
        # v1: a rejection cancels the owning workflow run.  Steps that need a
        # distinct rejection path can register a "reject" step name and set
        # payload["reject_step"]; the default behaviour is cancellation.
        reject_step = (item.payload_json or {}).get("reject_step")
        if reject_step is not None:
            continuation = _STEP_REGISTRY.get(run.workflow_type, {}).get(reject_step)
            if continuation is not None:
                step_result = continuation(db, run, item, user_id) or {}
        else:
            _cancel_run(db, run, reason)
        item.status = WorkItemStatus.REJECTED
    else:  # cancel
        _cancel_run(db, run, reason)
        item.status = WorkItemStatus.CANCELLED

    item.decision_json = {
        "decision": decision,
        "reason": reason,
        "user_id": str(user_id),
        "submitted_version": submitted_version,
        "decided_at": utc_now().isoformat(),
    }
    item.decided_by_user_id = user_id
    item.decided_at = utc_now()

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
        },
        correlation_id=run.correlation_id,
    )
    db.flush()
    return {
        "workItemId": str(item.id),
        "decision": decision,
        "status": item.status.value,
        "workflowId": str(run.id),
        "workflowStatus": run.status.value,
        "result": step_result,
    }


__all__ = [
    "FOUR_EYES_AREAS",
    "VALID_DECISIONS",
    "create_work_item",
    "get_work_item",
    "register_next_step",
    "submit_decision",
]
