"""DBOS workflow worker entrypoint: ``python -m app.worker``.

Configures logging/telemetry, bootstraps the DBOS runtime and blocks while the
worker runs. Importing this module has no side effects.
"""

from __future__ import annotations

import signal
import sys
import time

from app.core.logging import configure_logging, get_logger
from app.core.telemetry import setup_telemetry

_stop = False


def _handle_signal(signum: int, frame: object) -> None:
    """Request a graceful stop of the idle loop."""
    del signum, frame
    global _stop
    _stop = True


def _sleep_forever() -> None:
    """Keep the process alive until SIGINT/SIGTERM."""
    global _stop
    while not _stop:
        time.sleep(1)
    get_logger("commerce.worker").info("worker_stopping")


def main() -> int:
    """Start the DBOS worker; fall back to an idle loop if it returns."""
    configure_logging()
    setup_telemetry()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    logger = get_logger("commerce.worker")
    logger.info("worker_starting")
    try:
        from app.workflows.bootstrap import configure_dbos, start_worker

        configure_dbos()
        start_worker()  # blocks while the DBOS worker runs
    except Exception:
        logger.exception("worker_bootstrap_failed")
    logger.info("worker_idle_loop_entered")
    _sleep_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
