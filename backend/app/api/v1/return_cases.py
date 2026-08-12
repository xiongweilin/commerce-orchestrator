"""GET /v1/return-cases — return case list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.services.rbac import DOMAIN_READ_ROLES
from app.services.return_cases import list_return_cases

router = APIRouter(prefix="/v1", tags=["return-cases"])


@router.get("/return-cases")
def list_cases(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*DOMAIN_READ_ROLES["return_cases"]))],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List return cases (整改计划 §四.2 read matrix)."""
    return list_return_cases(db, status=status, limit=limit, offset=offset)
