"""Append-only audit log for sensitive / state-changing actions."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_correlation_id
from app.models.audit import AuditLog


def record_audit(
    db,
    *,
    actor_user_id: uuid.UUID | str | None = None,
    action: str,
    resource_type: str,
    resource_id: str,
    changes: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    """Append an audit log entry and flush it with the caller's transaction."""
    entry = AuditLog(
        actor_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id is not None else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id or get_correlation_id(),
        changes=changes or {},
    )
    db.add(entry)
    db.flush()
    return entry


__all__ = ["record_audit"]
