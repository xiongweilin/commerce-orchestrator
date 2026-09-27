"""Reconciliation endpoints: run list, detail and manual diff resolution."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.schemas.base import IDEMPOTENCY_KEY_HEADER
from app.services.rbac import (
    DOMAIN_READ_ROLES,
    RECONCILIATION_RESOLVE_ROLES,
)
from app.services.reconciliation import (
    get_reconciliation_run,
    list_reconciliation_runs,
)
from app.services.workflows import resolve_diff_with_idempotency

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
    _authorized: Annotated[bool, Depends(require_roles(*DOMAIN_READ_ROLES["reconciliation"]))],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List reconciliation runs (accountant / compliance / system_admin)."""
    return list_reconciliation_runs(db, limit=limit, offset=offset)


@router.get("/reconciliations/{run_id}")
def run_detail(
    run_id: uuid.UUID,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*DOMAIN_READ_ROLES["reconciliation"]))],
) -> dict[str, Any]:
    """Return a reconciliation run with its diffs (read matrix)."""
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
    _authorized: Annotated[bool, Depends(require_roles(*RECONCILIATION_RESOLVE_ROLES))],
    idempotency_key: Annotated[str, Header(alias=IDEMPOTENCY_KEY_HEADER)],
) -> DiffResolveResponse:
    """Manually resolve a MANUAL_RECONCILIATION diff (never auto-resolved).

    Requires ``accountant`` / ``system_admin`` (整改计划 §四.2) and a mandatory
    Idempotency-Key (§四.1): same key + same body replays the stored result, a
    different body under the same key is a 409.
    """
    result = resolve_diff_with_idempotency(
        db,
        run_id=run_id,
        diff_id=diff_id,
        note=body.note,
        resolver_user_id=user_id,
        idempotency_key=idempotency_key,
    )
    return DiffResolveResponse(**result)
