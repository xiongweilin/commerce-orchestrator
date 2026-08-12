"""GET /v1/me — current active user with database-authoritative roles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_claims, get_session
from app.models.identity import RoleAssignment, User

router = APIRouter(prefix="/v1", tags=["me"])


def _rfc3339(value: int | float) -> str:
    return datetime.fromtimestamp(float(value), UTC).isoformat().replace("+00:00", "Z")


@router.get("/me")
def me(
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    claims: Annotated[dict[str, Any], Depends(get_current_user_claims)],
) -> dict[str, Any]:
    """Return the authenticated active user.

    ``roles`` are the database-authoritative ``RoleAssignment`` rows; the JWT
    role claims are informational only. ``jwtExpiresAt`` mirrors the token's
    ``exp`` claim so the console can bound its session lifetime.
    """
    user = db.get(User, user_id)
    assignments = (
        db.execute(select(RoleAssignment).where(RoleAssignment.user_id == user_id))
        .scalars()
        .all()
    )
    roles = sorted({assignment.role.value for assignment in assignments})
    exp = claims.get("exp")
    return {
        "id": str(user.id),
        "username": user.display_name,
        "displayName": user.display_name,
        "email": user.email,
        "roles": roles,
        "isActive": user.is_active,
        "jwtExpiresAt": _rfc3339(exp) if exp is not None else None,
    }
