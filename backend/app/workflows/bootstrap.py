"""DBOS runtime bootstrap for the worker process (P7).

``configure_dbos`` never imports DBOS at module import time -- the ``dbos``
package is imported lazily inside functions so that ``app.services`` and unit
tests never require a live DBOS runtime.  Only this module,
``app.workflows.definitions`` imports
``dbos`` at all.

:func:`start_worker` launches the DBOS runtime and returns; the blocking
relay/heartbeat loop lives in ``app.worker.run_forever``.  Any launch failure
propagates to the caller, which must exit non-zero (never an idle loop).
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger("commerce.dbos")


def configure_dbos() -> dict[str, Any]:
    """Return the DBOS configuration for this application.

    Returns ``{"system_database_url": ..., "config": {...}}`` where ``config``
    is the :class:`dbos.DBOSConfig` (a ``TypedDict`` in DBOS 2.x).  The
    application database URL is the worker role's business database
    (``COMMERCE_WORKER_DATABASE_URL`` when configured, else ``database_url``)
    so that ``@DBOS.transaction`` functions can read/write the commerce
    tables through ``DBOS.sql_session``.
    """
    settings = get_settings()
    application_database_url = settings.worker_database_url or settings.database_url
    config: dict[str, Any] = {
        "name": "commerce-orchestrator",
        "system_database_url": settings.dbos_system_database_url,
        "application_database_url": application_database_url,
        "log_level": settings.log_level,
        "dbos_system_schema": "dbos",
    }
    return {
        "system_database_url": settings.dbos_system_database_url,
        "config": config,
    }


def _mask_dsn(url: str) -> str:
    """Mask the password part of a database URL for logs (never leak secrets)."""
    if "://" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" not in rest:
        return url
    userinfo, _, host = rest.rpartition("@")
    user = userinfo.split(":", 1)[0] if ":" in userinfo else userinfo
    return f"{scheme}://{user}:***@{host}"


def start_worker() -> None:
    """Initialize DBOS, register all workflow definitions and launch.

    Raises on any bootstrap / launch failure so the caller can exit non-zero
    (P7 五.1: worker must never fall into an idle loop when DBOS fails).
    """
    from dbos import DBOS, DBOSConfig

    configure_logging()
    cfg = configure_dbos()["config"]
    # Import module 会注册 @DBOS.workflow/@DBOS.step/
    # @DBOS.scheduled function，确保 launch 前进入 global registry：
    # definitions：v2 单一主线（workflow_version=2）。
    from app.workflows import definitions  # noqa: F401

    DBOS(config=DBOSConfig(**cfg))
    DBOS.launch()
    logger.info(
        "dbos_worker_ready",
        system_database_url=_mask_dsn(cfg["system_database_url"]),
    )


__all__ = ["configure_dbos", "start_worker"]
