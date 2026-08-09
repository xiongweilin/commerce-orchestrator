"""GET /v1/workflows/{id} — workflow status queries."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.services.workflows import get_workflow, list_workflows

router = APIRouter(prefix="/v1", tags=["workflows"])


@router.get("/workflows/{workflow_id}")
def get_workflow_detail(
    workflow_id: uuid.UUID,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
) -> dict[str, Any]:
    """Return workflow run detail (status, type, work items, timestamps)."""
    return get_workflow(db, workflow_id)


@router.get("/workflows")
def list_workflow_runs(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    status: str | None = None,
    workflow_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List workflow runs (operations console overview)."""
    return list_workflows(
        db,
        status=status,
        workflow_type=workflow_type,
        limit=limit,
        offset=offset,
    )
