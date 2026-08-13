"""DBOS worker workflows.

``configure_dbos`` / ``start_worker`` are imported eagerly; the DBOS-decorated
workflow functions live in ``app.workflows.definitions`` and are registered on
import, so importing this package never pulls in ``dbos`` at module import
time.
"""

from __future__ import annotations

from app.workflows.bootstrap import configure_dbos, start_worker

__all__ = ["configure_dbos", "start_worker"]
