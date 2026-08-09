"""Work-item facade: approval-inbox queries plus decision submission.

The decision/approval enforcement lives in :mod:`app.services.approvals`;
this module re-exports it under the ``work_items`` name used by the API layer
and adds inbox-list queries for the operations console.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.models.workflow import WorkItem, WorkItemStatus
from app.services.approvals import create_work_item, get_work_item, submit_decision

__all__ = ["create_work_item", "get_work_item", "list_work_items", "submit_decision"]


def list_work_items(
    db,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of work items (approval inbox), newest first."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(WorkItem)
    count_stmt = select(func.count()).select_from(WorkItem)
    if status:
        try:
            status_enum = WorkItemStatus(status)
        except ValueError as exc:
            raise ValidationError(f"unknown work item status: {status}") from exc
        stmt = stmt.where(WorkItem.status == status_enum)
        count_stmt = count_stmt.where(WorkItem.status == status_enum)
    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(stmt.order_by(WorkItem.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "workItemId": str(item.id),
                "workflowId": str(item.workflow_id),
                "kind": item.kind.value,
                "title": item.title,
                "status": item.status.value,
                "payload": item.payload_json or {},
                "expectedWorkflowVersion": item.expected_version,
                "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
                "createdAt": item.created_at.isoformat(),
            }
            for item in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
