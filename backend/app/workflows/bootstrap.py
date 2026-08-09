"""DBOS runtime bootstrap for the worker process.

``configure_dbos`` never imports DBOS at module import time -- the ``dbos``
package is imported lazily inside functions so that ``app.services`` and unit
tests never require a live DBOS runtime.  Only this module and
``app.workflows.vertical_slice`` import ``dbos`` at all.
"""

from __future__ import annotations

import signal
import threading
from typing import Any

from app.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger("commerce.dbos")


def configure_dbos() -> dict[str, Any]:
    """Return the DBOS configuration for this application.

    Returns ``{"system_database_url": ..., "config": {...}}`` where ``config``
    is the :class:`dbos.DBOSConfig` (a ``TypedDict`` in DBOS 2.x).  The
    application database URL is the business database so that
    ``@DBOS.transaction`` functions can read/write the commerce tables through
    ``DBOS.sql_session``.
    """
    settings = get_settings()
    config: dict[str, Any] = {
        "name": "commerce-orchestrator",
        "system_database_url": settings.dbos_system_database_url,
        "application_database_url": settings.database_url,
        "log_level": settings.log_level,
        "dbos_system_schema": "dbos",
    }
    return {
        "system_database_url": settings.dbos_system_database_url,
        "config": config,
    }


def _block_until_signalled() -> None:
    """Block the main thread until SIGINT/SIGTERM (graceful shutdown)."""
    stop = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:  # noqa: ARG001
        logger.info("dbos_worker_shutdown_signal", signum=signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # Not on the main thread / unsupported on this platform.
            continue
    while not stop.wait(1.0):
        pass


def start_worker() -> None:
    """Initialize DBOS, register workflows, launch and block forever."""
    from dbos import DBOS, DBOSConfig

    configure_logging()
    cfg = configure_dbos()["config"]
    # Importing the slice registers the @DBOS.workflow/@DBOS.step/@DBOS.scheduled
    # functions in the global registry before launch.
    from app.workflows import vertical_slice  # noqa: F401

    DBOS(config=DBOSConfig(**cfg))
    DBOS.launch()
    logger.info("dbos_worker_ready", system_database_url=cfg["system_database_url"])
    try:
        _block_until_signalled()
    finally:
        DBOS.destroy()
        logger.info("dbos_worker_stopped")


__all__ = ["configure_dbos", "start_worker"]
