"""DBOS worker workflows.

``configure_dbos`` / ``start_worker`` are imported eagerly; the DBOS-decorated
workflow functions live in ``app.workflows.definitions`` and are registered on
import, so importing this package never pulls in ``dbos`` at module import
time.

The webhook ``return-to-refund`` workflow intentionally shares the ReturnCase
continuations used by ``return``.  ``continuation_aliases`` registers that
parity explicitly because the approval resolver is strict on workflow type.
"""

from __future__ import annotations

from app.workflows import continuation_aliases as _continuation_aliases  # noqa: F401
from app.workflows.bootstrap import configure_dbos, start_worker

__all__ = ["configure_dbos", "start_worker"]
