"""POST /v1/work-items/{id}/decisions — approval decisions on work items.

Decisions route through the WP4 ``submit_decision`` facade
(``app.services.workflows``) which implements the Idempotency-Key semantics
when the header is present. The header stays optional for backward
compatibility with the current console decision form and the existing API
tests (整改计划 §四.1 asks for uniform Idempotency-Key; tightening is tracked
in WP6-REPORT.md). Per-work-item role enforcement, four-eyes and compliance
veto live in ``app.services.approvals``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.schemas.base import IDEMPOTENCY_KEY_HEADER
from app.schemas.commands import WorkItemDecisionSubmit
from app.schemas.events import ROLES
from app.services.work_items import list_work_items
from app.services.workflows import submit_decision

router = APIRouter(prefix="/v1", tags=["decisions"])


class WorkItemDecisionResponse(BaseModel):
    """Result of a submitted work item decision."""

    workItemId: uuid.UUID
    status: str
    workflowId: uuid.UUID


@router.post("/work-items/{work_item_id}/decisions", response_model=WorkItemDecisionResponse)
def submit_work_item_decision(
    work_item_id: uuid.UUID,
    body: WorkItemDecisionSubmit,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> WorkItemDecisionResponse:
    """Submit a decision on a pending work item.

    Permission enforcement is per work item kind (approval / confirmation /
    manual step) inside ``app.services.work_items.submit_decision``; role and
    four-eyes violations surface as 403 ``permission_denied``, an
    ``expectedWorkflowVersion`` mismatch as 409 ``workflow_version_conflict``.
    Idempotency-Key is required (整改计划 §四.1): same key + same body replays
    the stored result, a different body under the same key is a 409.
    """
    result = submit_decision(
        work_item_id=work_item_id,
        actor=user_id,
        decision=body.decision,
        expected_version=body.expectedWorkflowVersion,
        idempotency_key=idempotency_key,
        db=db,
        reason=body.reason,
    )
    return WorkItemDecisionResponse(
        workItemId=result.workItemId,
        status=result.status,
        workflowId=result.workflowId,
    )


@router.get("/work-items")
def list_pending_work_items(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*ROLES))],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List work items (approval inbox) for the operations console.

    Read access requires any valid business role (整改计划 §四.2).
    """
    return list_work_items(db, status=status, limit=limit, offset=offset)
