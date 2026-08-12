"""V2 test helpers: seed DBOS v2 runs and apply decisions like the worker.

These mirror what the DBOS worker does (``app.workflows.definitions._start_txn``
and ``_apply_decision_txn``) so approval/domain unit tests run without a live
DBOS runtime.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.uuid7 import uuid7
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem
from app.services.approvals import apply_domain_continuation, submit_decision
from app.services.commands import (
    COMMAND_HANDLERS,
    DBOS_ORCHESTRATION_ENGINE,
    DBOS_WORKFLOW_VERSION,
)


def start_v2_run(
    db,
    command_type: str,
    payload: dict[str, Any],
    *,
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> tuple[WorkflowRun, list[WorkItem]]:
    """Create a DBOS v2 run and run the shared domain entry (like wf2_start)."""
    correlation_id = correlation_id or str(uuid7())
    run = WorkflowRun(
        workflow_type=command_type,
        workflow_version=DBOS_WORKFLOW_VERSION,
        orchestration_engine=DBOS_ORCHESTRATION_ENGINE,
        status=WorkflowRunStatus.ACCEPTED,
        correlation_id=correlation_id,
        input_json=payload,
        initiated_by_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id else None,
    )
    db.add(run)
    db.flush()
    handler = COMMAND_HANDLERS.get(command_type)
    if handler is None:
        raise ValueError(f"no domain entry for command type {command_type!r}")
    handler(db, run, payload, actor_user_id, correlation_id)
    db.flush()
    items = list(
        db.execute(
            select(WorkItem).where(WorkItem.workflow_id == run.id).order_by(WorkItem.created_at)
        )
        .scalars()
        .all()
    )
    return run, items


def apply_v2_decision(
    db,
    *,
    work_item_id: uuid.UUID,
    user_id: uuid.UUID,
    decision: str,
    reason: str | None = None,
    expected_workflow_version: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """submit_decision + the worker-side continuation (mirrors wf2_apply_decision)."""
    result = submit_decision(
        db,
        work_item_id=work_item_id,
        user_id=user_id,
        decision=decision,
        reason=reason,
        expected_workflow_version=expected_workflow_version,
        idempotency_key=idempotency_key,
    )
    run = db.get(WorkflowRun, uuid.UUID(str(result.workflow_id)))
    item = db.get(WorkItem, uuid.UUID(str(work_item_id)))
    run.version += 1
    apply_domain_continuation(
        db,
        run=run,
        item=item,
        user_id=uuid.UUID(str(user_id)),
        decision=decision,
        reason=reason,
    )
    db.flush()
    return result