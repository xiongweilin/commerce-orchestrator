"""Role-based access control helpers backed by the identity tables."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.errors import PermissionDeniedError
from app.models.identity import RoleAssignment
from app.schemas.events import ROLES


def has_role(db, user_id: uuid.UUID | str, role: str, scope: str = "*") -> bool:
    """Return True when the user holds ``role`` in ``scope``.

    ``scope="*"`` matches any assignment scope; otherwise an assignment in the
    exact scope or the ``"*"`` wildcard scope satisfies the check.
    """
    if role not in ROLES:
        return False
    uid = uuid.UUID(str(user_id))
    stmt = select(RoleAssignment.id).where(
        RoleAssignment.user_id == uid,
        RoleAssignment.role == role,
    )
    if scope != "*":
        stmt = stmt.where(RoleAssignment.scope.in_((scope, "*")))
    return db.execute(stmt.limit(1)).first() is not None


def ensure_roles(db, user_id: uuid.UUID | str, roles: list[str], scope: str = "*") -> None:
    """Raise :class:`PermissionDeniedError` unless the user holds one role."""
    if not roles:
        return
    if not any(has_role(db, user_id, role, scope=scope) for role in roles):
        raise PermissionDeniedError(f"requires one of roles: {', '.join(roles)}")


__all__ = ["ensure_roles", "has_role"]
