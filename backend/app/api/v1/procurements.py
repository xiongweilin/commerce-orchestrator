"""GET /v1/procurements — procurement order list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.services.procurements import list_procurements
from app.services.rbac import DOMAIN_READ_ROLES

router = APIRouter(prefix="/v1", tags=["procurements"])


@router.get("/procurements")
def list_procurement_orders(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*DOMAIN_READ_ROLES["procurements"]))],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List procurement orders (整改计划 §四.2 read matrix)."""
    return list_procurements(db, status=status, limit=limit, offset=offset)
