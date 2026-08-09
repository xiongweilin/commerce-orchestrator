"""Shared API dependencies: authentication, RBAC, session and correlation id.

The session factory and the correlation id accessor are re-exported here so
routers only import from ``app.api.deps``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import CommerceError, PermissionDeniedError
from app.core.logging import get_correlation_id
from app.core.security import decode_jwt
from app.models.identity import Role, RoleAssignment
from app.schemas.events import ROLES

__all__ = [
    "UnauthenticatedError",
    "get_correlation_id",
    "get_current_user",
    "get_session",
    "require_roles",
]


class UnauthenticatedError(CommerceError):
    """HTTP 401: the request lacks valid authentication credentials."""

    status_code = 401
    type = "unauthenticated"
    title = "Authentication required"


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise UnauthenticatedError("Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthenticatedError("Authorization header must use the Bearer scheme")
    return token


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """Resolve the authenticated user id from a Bearer JWT.

    The JWT subject is the user id (UUIDv7). Missing or invalid credentials
    produce a 401 ``unauthenticated`` envelope.
    """
    try:
        claims = decode_jwt(_bearer_token(authorization))
        return uuid.UUID(claims["sub"])
    except (PermissionDeniedError, KeyError, TypeError, ValueError) as exc:
        raise UnauthenticatedError("Invalid or expired token") from exc


def require_roles(*roles: str) -> Callable[..., bool]:
    """Dependency factory: the caller must hold at least one of ``roles``.

    Role strings are validated against the fixed vocabulary from
    ``app.schemas.events.ROLES`` at decoration time (fail fast). The returned
    dependency reads the current user's role assignments from the database and
    raises 403 ``permission_denied`` when none of the required roles is held.
    """
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"Unknown role(s): {sorted(unknown)}")
    required = {Role(role) for role in roles}

    def _check(
        db: Session = Depends(get_session),
        user_id: uuid.UUID = Depends(get_current_user),
    ) -> bool:
        stmt = (
            select(RoleAssignment.id)
            .where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role.in_(required),
            )
            .limit(1)
        )
        if db.execute(stmt).scalar_one_or_none() is None:
            raise PermissionDeniedError(f"Missing required role(s): {', '.join(roles)}")
        return True

    return _check
