"""POST /v1/work-items/{id}/decisions — approval decisions on work items."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.schemas.commands import WorkItemDecisionSubmit
from app.services.work_items import list_work_items, submit_decision

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
) -> WorkItemDecisionResponse:
    """Submit a decision on a pending work item.

    Permission enforcement is per work item kind (approval / confirmation /
    manual step) inside ``app.services.work_items.submit_decision``; role and
    four-eyes violations surface as 403 ``permission_denied``, an
    ``expectedWorkflowVersion`` mismatch as 409 ``workflow_version_conflict``.
    """
    result = submit_decision(
        db,
        work_item_id=work_item_id,
        user_id=user_id,
        decision=body.decision,
        reason=body.reason,
        expected_workflow_version=body.expectedWorkflowVersion,
    )
    return WorkItemDecisionResponse.model_validate(result)


@router.get("/work-items")
def list_pending_work_items(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List work items (approval inbox) for the operations console."""
    return list_work_items(db, status=status, limit=limit, offset=offset)
