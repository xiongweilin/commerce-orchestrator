"""Shared API dependencies: authentication, RBAC, session and correlation id.

The session factory and the correlation id accessor are re-exported here so
routers only import from ``app.api.deps``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header
from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.errors import CommerceError, PermissionDeniedError
from app.core.logging import get_correlation_id
from app.core.security import decode_jwt
from app.models.identity import Role, RoleAssignment, User
from app.schemas.events import ROLES
from app.services.audit import record_audit

__all__ = [
    "UnauthenticatedError",
    "get_correlation_id",
    "get_current_user",
    "get_current_user_claims",
    "get_session",
    "require_roles",
]

RBAC_DENIALS = Counter(
    "commerce_rbac_denials_total",
    "RBAC permission denials raised by the API layer",
    ["domain"],
)


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


def _authenticate(db: Session, authorization: str | None) -> tuple[uuid.UUID, dict[str, Any]]:
    """Verify the Bearer JWT, load the user and enforce active status.

    JWT role claims are informational only; permissions are resolved from the
    database ``RoleAssignment`` rows by ``require_roles``. An inactive or
    unknown user is treated as unauthenticated (401).
    """
    try:
        claims = decode_jwt(_bearer_token(authorization))
        user_id = uuid.UUID(claims["sub"])
    except (PermissionDeniedError, KeyError, TypeError, ValueError) as exc:
        raise UnauthenticatedError("Invalid or expired token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthenticatedError("User is inactive or does not exist")
    return user_id, claims


def get_current_user(
    db: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """Resolve the authenticated, active user id from a Bearer JWT."""
    user_id, _ = _authenticate(db, authorization)
    return user_id


def get_current_user_claims(
    db: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Return the verified JWT claims of the authenticated active user."""
    _, claims = _authenticate(db, authorization)
    return claims


def require_roles(*roles: str) -> Callable[..., bool]:
    """Dependency factory: the caller must hold at least one of ``roles``.

    Role strings are validated against the fixed vocabulary from
    ``app.schemas.events.ROLES`` at decoration time (fail fast). The returned
    dependency reads the current user's role assignments from the database and
    raises 403 ``permission_denied`` when none of the required roles is held.
    Every denial is appended to the audit log (without token, request body or
    PII) and counted on the ``rbac_denials_total`` metric.
    """
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"Unknown role(s): {sorted(unknown)}")
    required = {Role(role) for role in roles}
    domain = "|".join(sorted(roles))

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
            RBAC_DENIALS.labels(domain=domain).inc()
            record_audit(
                db,
                actor_user_id=user_id,
                action="rbac.denied",
                resource_type="rbac",
                resource_id=f"rbac:{domain}",
                changes={"required_roles": sorted(roles)},
                correlation_id=get_correlation_id(),
            )
            # The request-scoped session rolls back when this dependency
            # raises, so commit the audit row before surfacing the denial.
            db.commit()
            raise PermissionDeniedError(f"Missing required role(s): {', '.join(roles)}")
        return True

    return _check
