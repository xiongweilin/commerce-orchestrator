"""GET /v1/return-cases — return case list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.services.return_cases import list_return_cases

router = APIRouter(prefix="/v1", tags=["return-cases"])


@router.get("/return-cases")
def list_cases(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List return cases (operations console domain view)."""
    return list_return_cases(db, status=status, limit=limit, offset=offset)
