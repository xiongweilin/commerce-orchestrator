"""Workflow status queries (pollable status URL backend)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from app.core.errors import NotFoundError, ValidationError
from app.models.effect import EffectLedgerEntry
from app.models.messaging import OutboxEvent
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem, WorkItemStatus


def get_work_item(db, work_item_id: uuid.UUID) -> WorkItem:
    from app.services.approvals import get_work_item as _get  # re-export convenience

    return _get(db, work_item_id)


def _work_item_summary(item: WorkItem) -> dict[str, Any]:
    return {
        "workItemId": str(item.id),
        "kind": item.kind.value,
        "title": item.title,
        "status": item.status.value,
        "requiredRoles": item.required_roles or [],
        "assigneeUserId": str(item.assignee_user_id) if item.assignee_user_id else None,
        "expectedVersion": item.expected_version,
        "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
        "decidedByUserId": str(item.decided_by_user_id) if item.decided_by_user_id else None,
        "decidedAt": item.decided_at.isoformat() if item.decided_at else None,
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
            }
            for effect in effects
        ],
        "workItems": [_work_item_summary(item) for item in work_items],
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
    }


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


__all__ = ["get_work_item", "get_workflow", "list_workflows"]
