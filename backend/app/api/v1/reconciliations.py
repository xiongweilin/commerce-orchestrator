"""Reconciliation endpoints: run list, detail and manual diff resolution."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.services.reconciliation import (
    get_reconciliation_run,
    list_reconciliation_runs,
    resolve_diff,
)

router = APIRouter(prefix="/v1", tags=["reconciliations"])


class DiffResolveRequest(BaseModel):
    """Manual resolution note for a reconciliation diff."""

    note: str


class DiffResolveResponse(BaseModel):
    """Result of resolving a reconciliation diff."""

    diffId: uuid.UUID
    status: str
    resolvedAt: str | None = None


@router.get("/reconciliations")
def list_runs(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List reconciliation runs (newest first)."""
    return list_reconciliation_runs(db, limit=limit, offset=offset)


@router.get("/reconciliations/{run_id}")
def run_detail(
    run_id: uuid.UUID,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
) -> dict[str, Any]:
    """Return a reconciliation run with its diffs."""
    return get_reconciliation_run(db, run_id)


@router.post(
    "/reconciliations/{run_id}/diffs/{diff_id}/resolve", response_model=DiffResolveResponse
)
def resolve_diff_endpoint(
    run_id: uuid.UUID,
    diff_id: uuid.UUID,
    body: DiffResolveRequest,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
) -> DiffResolveResponse:
    """Manually resolve a MANUAL_RECONCILIATION diff (never auto-resolved)."""
    diff = resolve_diff(db, diff_id=diff_id, note=body.note, resolver_user_id=user_id)
    return DiffResolveResponse(
        diffId=diff.id,
        status=diff.status.value,
        resolvedAt=diff.resolved_at.isoformat() if diff.resolved_at else None,
    )
