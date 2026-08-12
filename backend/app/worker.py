"""DBOS worker entrypoint: ``python -m app.worker`` (P7).

Responsibilities:

- bootstrap DBOS (v2 definitions + v1 legacy slice) and **exit non-zero** on
  any bootstrap/launch failure — there is no idle-loop fallback;
- run the inbox relay loop (SKIP LOCKED claim, lease recovery, backoff,
  dead-letter) for the ``worker`` consumer;
- heartbeat ``runtime_heartbeat`` (upsert semantics per WP1);
- serve worker metrics + liveness on ``COMMERCE_WORKER_METRICS_PORT`` (9101);
- run the daily privacy retention cleanup / encrypted backfill entry points.

Importing this module has no side effects and never imports ``dbos``.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.telemetry import setup_telemetry
from app.core.time import utc_now
from app.models.messaging import InboxEvent, InboxStatus
from app.models.runtime import RuntimeHeartbeat

WORKER_PROCESS_NAME = "worker"
WORKER_CONSUMER = "worker"

_stop = False


def _logger():
    """Return the structured worker logger (lazy: no settings read at import)."""
    return get_logger("commerce.worker")


def _handle_signal(signum: int, frame: object) -> None:
    """Request a graceful stop of the relay loop."""
    del signum, frame
    global _stop
    _stop = True


def _session_factory() -> Callable[[], Session]:
    """Return a session factory bound to the worker database URL when set."""
    settings = get_settings()
    if settings.worker_database_url:
        engine = create_engine(settings.worker_database_url, pool_pre_ping=True)
        return sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    from app.core.db import SessionLocal

    return SessionLocal


def _upsert_heartbeat(
    session: Session,
    *,
    instance_id: str,
    started_at: datetime,
    details: dict,
) -> None:
    """Upsert the worker heartbeat row (started_at never refreshed)."""
    row = session.execute(
        select(RuntimeHeartbeat).where(
            RuntimeHeartbeat.process_name == WORKER_PROCESS_NAME,
            RuntimeHeartbeat.instance_id == instance_id,
        )
    ).scalar_one_or_none()
    now = utc_now()
    if row is None:
        session.add(
            RuntimeHeartbeat(
                process_name=WORKER_PROCESS_NAME,
                instance_id=instance_id,
                status="running",
                started_at=started_at,
                heartbeat_at=now,
                details_json=details,
            )
        )
    else:
        row.status = "running"
        row.heartbeat_at = now
        row.details_json = details
    session.commit()


def _inbox_counts(session: Session, consumer: str) -> tuple[int, int, int]:
    def _count(status: InboxStatus) -> int:
        return session.execute(
            select(func.count()).select_from(InboxEvent).where(
                InboxEvent.consumer == consumer,
                InboxEvent.status == status,
            )
        ).scalar_one()

    return _count(InboxStatus.PENDING), _count(InboxStatus.PROCESSING), _count(InboxStatus.FAILED)


def _relay_pass(session_factory: Callable[[], Session]) -> None:
    """One relay iteration: recover leases, claim + dispatch, snapshot metrics."""
    from app.services.outbox_inbox import (
        recover_expired_leases,
        relay_inbox_batch,
    )
    from app.workflows.inbox_dispatch import dispatch_inbox_event
    from app.workflows.metrics import set_inbox_gauges

    settings = get_settings()
    logger = _logger()
    with session_factory() as session:
        recovered = recover_expired_leases(session, consumer=WORKER_CONSUMER)
        if recovered:
            logger.warning("worker_recovered_expired_leases", count=recovered)
        stats = relay_inbox_batch(
            session,
            consumer=WORKER_CONSUMER,
            dispatch=dispatch_inbox_event,
            batch=settings.inbox_batch_size,
            lease_seconds=settings.inbox_lease_seconds,
            max_attempts=settings.inbox_max_attempts,
        )
        pending, processing, failed = _inbox_counts(session, WORKER_CONSUMER)
        set_inbox_gauges(WORKER_CONSUMER, pending, processing, failed)
        if stats.dead_lettered:
            logger.error(
                "worker_inbox_dead_lettered",
                count=stats.dead_lettered,
                errors=stats.errors,
            )
        if stats.claimed:
            logger.info(
                "worker_relay_pass",
                claimed=stats.claimed,
                processed=stats.processed,
                retried=stats.retried,
                dead_lettered=stats.dead_lettered,
            )


def _privacy_pass(
    session_factory: Callable[[], Session],
    *,
    last_cleanup: datetime | None,
    now: datetime,
) -> datetime | None:
    """Run the retention cleanup when due; backfill runs once at startup."""
    from app.services.privacy import (
        cleanup_expired_payloads,
        should_run_cleanup,
    )
    from app.workflows.metrics import (
        WORKER_PRIVACY_OVERDUE_AGE,
        record_privacy_cleanup,
    )

    logger = _logger()
    if not should_run_cleanup(last_run=last_cleanup, now=now):
        return last_cleanup
    with session_factory() as session:
        stats = cleanup_expired_payloads(session)
        record_privacy_cleanup(stats.cleared)
        if stats.oldest_overdue_age_seconds is not None:
            WORKER_PRIVACY_OVERDUE_AGE.set(stats.oldest_overdue_age_seconds)
        logger.info(
            "worker_privacy_cleanup",
            cleared=stats.cleared,
            errors=stats.errors,
        )
    return now


def run_forever(
    *,
    session_factory: Callable[[], Session],
    instance_id: str,
    started_at: datetime,
    poll_interval_ms: int | None = None,
) -> None:
    """Block the main thread: relay loop + heartbeat + privacy jobs."""
    from app.workflows.metrics import set_heartbeat_timestamp, start_metrics_server

    settings = get_settings()
    logger = _logger()
    start_metrics_server(settings.worker_metrics_port)
    logger.info(
        "worker_metrics_ready",
        port=settings.worker_metrics_port,
        instance_id=instance_id,
    )
    poll_seconds = max(0.05, (poll_interval_ms or settings.inbox_poll_interval_ms) / 1000.0)
    heartbeat_interval = settings.worker_heartbeat_interval_seconds

    last_heartbeat = 0.0
    last_cleanup: datetime | None = None
    while not _stop:
        now = time.monotonic()
        try:
            _relay_pass(session_factory)
            if now - last_heartbeat >= heartbeat_interval:
                with session_factory() as session:
                    _upsert_heartbeat(
                        session,
                        instance_id=instance_id,
                        started_at=started_at,
                        details={"metrics_port": settings.worker_metrics_port},
                    )
                set_heartbeat_timestamp(time.time())
                last_heartbeat = now
            last_cleanup = _privacy_pass(
                session_factory,
                last_cleanup=last_cleanup,
                now=utc_now(),
            )
        except Exception:  # noqa: BLE001 - the loop must survive a bad iteration
            logger.exception("worker_iteration_failed")
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    """Start the DBOS worker; any bootstrap failure exits non-zero."""
    parser = argparse.ArgumentParser(
        prog="python -m app.worker",
        description="Commerce orchestrator DBOS v2 worker (relay + heartbeat + metrics).",
    )
    parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=None,
        help="Override COMMERCE_INBOX_POLL_INTERVAL_MS for this process.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    setup_telemetry()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    logger = _logger()
    logger.info("worker_starting")

    try:
        from app.workflows.bootstrap import start_worker

        start_worker()  # raises on bootstrap / DBOS launch failure
    except Exception:
        logger.exception("worker_bootstrap_failed")
        return 1

    session_factory = _session_factory()
    instance_id = str(uuid.uuid4())
    started_at = utc_now()
    logger.info("worker_running", instance_id=instance_id)
    try:
        run_forever(
            session_factory=session_factory,
            instance_id=instance_id,
            started_at=started_at,
            poll_interval_ms=args.poll_interval_ms,
        )
    except Exception:  # noqa: BLE001 - relay loop crashed unexpectedly
        logger.exception("worker_run_failed")
        return 1
    finally:
        try:
            from dbos import DBOS

            DBOS.destroy()
        except Exception:  # noqa: BLE001 - shutdown cleanup must not mask status
            logger.exception("dbos_destroy_failed")
    logger.info("worker_stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
