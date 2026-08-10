"""GET /v1/procurements — procurement order list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.services.procurements import list_procurements

router = APIRouter(prefix="/v1", tags=["procurements"])


@router.get("/procurements")
def list_procurement_orders(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List procurement orders (operations console domain view)."""
    return list_procurements(db, status=status, limit=limit, offset=offset)
