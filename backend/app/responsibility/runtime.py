"""Durable portable-runtime responsibility kernel for Commerce workers.

Commerce PostgreSQL remains authoritative for domain facts, Decisions,
ExecutionAuthorization, effects and verified outcomes.  This module owns only
the portable responsibility coordination journal.  The two stores are not a
single transaction domain; external effects therefore continue to revalidate
Commerce-owned current facts and authorization independently.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from portable_runtime.responsibility import ResponsibilityKernel
from portable_runtime.stores.sqlite import SQLiteStateStore

from app.config import get_settings

_kernel: ResponsibilityKernel | None = None
_kernel_lock = Lock()


def get_responsibility_kernel() -> ResponsibilityKernel:
    """Return the process-local kernel backed by the configured durable store."""

    global _kernel
    with _kernel_lock:
        if _kernel is None:
            state_path = Path(get_settings().responsibility_state_path)
            _kernel = ResponsibilityKernel(SQLiteStateStore(state_path))
        return _kernel


def close_responsibility_kernel() -> None:
    """Close the durable store without changing persisted responsibility state."""

    global _kernel
    with _kernel_lock:
        if _kernel is None:
            return
        close = getattr(_kernel.store, "close", None)
        if callable(close):
            close()
        _kernel = None


__all__ = ["close_responsibility_kernel", "get_responsibility_kernel"]
