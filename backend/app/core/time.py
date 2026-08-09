"""Time helpers. All persisted timestamps are UTC-aware datetimes."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


__all__ = ["utc_now"]
