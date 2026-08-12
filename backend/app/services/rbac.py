"""Role-based access control helpers backed by the identity tables.

This module is the single source of truth for the strict domain RBAC matrix
(整改计划 §四.2).  Command initiation, read access and ops access are declared
here; the API layer enforces them through ``app.api.deps.require_roles`` with
database ``RoleAssignment`` rows (JWT role claims are informational only).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.errors import PermissionDeniedError
from app.models.identity import RoleAssignment
from app.schemas.events import ROLES

# Workflow types that map one-to-one to their command scope.
COMMAND_INITIATE_ROLES: dict[str, tuple[str, ...]] = {
    "catalog-revision": ("catalog_owner",),
    "listing-publication": ("catalog_owner",),
    "procurement": ("procurement_lead",),
    "return": ("customer_service",),
    "reconciliation": ("accountant", "system_admin"),
}

# Read matrix (整改计划 §四.2): domain -> allowed roles.
DOMAIN_READ_ROLES: dict[str, tuple[str, ...]] = {
    # workflows / work items: any active user holding at least one role.
    "workflows": tuple(ROLES),
    "work_items": tuple(ROLES),
    "sales_orders": (
        "customer_service",
        "warehouse_staff",
        "finance_approver",
        "accountant",
        "system_admin",
    ),
    "return_cases": (
        "customer_service",
        "warehouse_staff",
        "finance_approver",
        "accountant",
        "system_admin",
    ),
    "procurements": (
        "procurement_lead",
        "budget_owner",
        "warehouse_staff",
        "accountant",
        "system_admin",
    ),
    "reconciliation": ("accountant", "compliance", "system_admin"),
    "ops": ("system_admin",),
}

# Diff resolve is a reconciliation write: accountant / system_admin only.
RECONCILIATION_RESOLVE_ROLES: tuple[str, ...] = ("accountant", "system_admin")

# Failed inbox retry: system_admin only.
OPS_RETRY_ROLES: tuple[str, ...] = ("system_admin",)


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


__all__ = [
    "COMMAND_INITIATE_ROLES",
    "DOMAIN_READ_ROLES",
    "OPS_RETRY_ROLES",
    "RECONCILIATION_RESOLVE_ROLES",
    "ensure_roles",
    "has_role",
]
