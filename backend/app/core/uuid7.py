"""UUIDv7 helpers for time-ordered primary keys and event ids."""

from __future__ import annotations

import uuid

import uuid6


def uuid7() -> uuid.UUID:
    """Return a new time-ordered UUIDv7."""
    return uuid6.uuid7()


__all__ = ["uuid7"]
