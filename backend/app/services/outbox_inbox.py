"""Outbox / inbox event pipeline.

Every domain event is written to the outbox (``outbox_event``) and copied into
the inbox (``inbox_event``) of every local consumer.  Delivery is
at-least-once: consumers must be idempotent and deduplicate on
``(consumer, event_id)`` (enforced by a unique constraint on the table).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.messaging import InboxEvent, InboxStatus, OutboxEvent, OutboxStatus
from app.schemas.events import EVENT_TYPES

logger = get_logger("commerce.outbox")

# Reserved payload keys that carry W3C trace context on the wire envelope.
TRACE_PARENT_KEY = "traceparent"
TRACE_STATE_KEY = "tracestate"

DEFAULT_RELAY_BATCH = 50
DEFAULT_RELAY_LEASE_SECONDS = 30
DEFAULT_RELAY_MAX_ATTEMPTS = 10
MAX_BACKOFF_SECONDS = 60.0

PRODUCERS = frozenset(
    (
        "feedback_intelligence",
        "operating_policy",
        "catalog",
        "listing",
        "order",
        "procurement",
        "return",
        "workflow",
        "effect",
        "shopify_adapter",
        "odoo_adapter",
    )
)

# producer -> event-domain prefixes it is allowed to emit (empty = any domain).
_PRODUCER_DOMAINS: dict[str, frozenset[str]] = {
    "feedback_intelligence": frozenset({"feedback"}),
    "operating_policy": frozenset(),
    "catalog": frozenset({"catalog"}),
    "listing": frozenset({"listing"}),
    "order": frozenset({"order"}),
    "procurement": frozenset({"procurement"}),
    "return": frozenset({"return"}),
    "workflow": frozenset({"workflow"}),
    "effect": frozenset({"effect"}),
    "shopify_adapter": frozenset({"order", "return", "listing", "catalog"}),
    "odoo_adapter": frozenset({"order", "procurement", "catalog", "listing"}),
}

# event_type (or "*") -> local consumers that receive a copy in the inbox.
_LOCAL_CONSUMER_ROUTING: dict[str, list[str]] = {}

# consumer -> handler invoked by process_outbox (optional).
CONSUMER_HANDLERS: dict[str, Callable[[OutboxEvent], None]] = {}


def register_local_consumer(event_type: str, consumer: str) -> None:
    """Route ``event_type`` (or ``"*"`` for everything) to a local consumer."""
    consumers = _LOCAL_CONSUMER_ROUTING.setdefault(event_type, [])
    if consumer not in consumers:
        consumers.append(consumer)


def local_consumers_for(event_type: str) -> list[str]:
    """Consumers subscribed to this event type (exact match plus ``"*"``)."""
    return list(_LOCAL_CONSUMER_ROUTING.get(event_type, [])) + list(
        _LOCAL_CONSUMER_ROUTING.get("*", [])
    )


def register_consumer(consumer: str, handler: Callable[[OutboxEvent], None]) -> None:
    """Register the in-process handler used by :func:`process_outbox`."""
    CONSUMER_HANDLERS[consumer] = handler


def _validate_producer(event_type: str, producer: str) -> None:
    if producer not in PRODUCERS:
        raise ValidationError(f"unknown producer: {producer}")
    domain = event_type.split(".", 1)[0]
    allowed = _PRODUCER_DOMAINS[producer]
    if allowed and domain not in allowed:
        raise ValidationError(f"producer {producer!r} cannot emit {event_type!r}")


def _insert_inbox(db, consumer: str, event_id: uuid.UUID) -> bool:
    """Insert an inbox row, tolerating duplicate (consumer, event_id) rows."""
    try:
        with db.begin_nested():
            db.add(InboxEvent(consumer=consumer, event_id=event_id))
        return True
    except IntegrityError:
        return False


def emit_event(
    db,
    *,
    event_id: uuid.UUID | None = None,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int = 1,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    producer: str,
    payload: dict[str, Any] | None = None,
    schema_version: str = "1.0",
    consumers: list[str] | None = None,
    traceparent: str | None = None,
    tracestate: str | None = None,
) -> OutboxEvent:
    """Write an outbox event and copy it into local consumer inboxes."""
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unknown event type: {event_type}")
    if not aggregate_type or not aggregate_id:
        raise ValidationError("aggregate_type and aggregate_id are required")
    _validate_producer(event_type, producer)

    event_id = event_id or uuid7()
    occurred_at = occurred_at or utc_now()
    event_payload = dict(payload or {})
    # Trace context rides inside the stored payload (no dedicated columns yet)
    # and is hoisted onto the wire envelope by :func:`envelope_for`.
    if traceparent is not None:
        event_payload[TRACE_PARENT_KEY] = traceparent
    if tracestate is not None:
        event_payload[TRACE_STATE_KEY] = tracestate
    event = OutboxEvent(
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version or 1,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        causation_id=causation_id,
        producer=producer,
        schema_version=schema_version,
        payload=event_payload,
    )
    db.add(event)
    targets = consumers if consumers is not None else local_consumers_for(event_type)
    for consumer in targets:
        _insert_inbox(db, consumer, event_id)
    db.flush()
    return event


def envelope_for(event: OutboxEvent) -> dict[str, Any]:
    """Return the wire envelope for an outbox event (JSON-safe dict)."""
    payload = dict(event.payload or {})
    return {
        "eventId": str(event.event_id),
        "type": event.event_type,
        "aggregateType": event.aggregate_type,
        "aggregateId": event.aggregate_id,
        "aggregateVersion": event.aggregate_version,
        "occurredAt": event.occurred_at.isoformat(),
        "correlationId": event.correlation_id,
        "causationId": event.causation_id,
        "producer": event.producer,
        "schemaVersion": event.schema_version,
        "payload": {
            k: v
            for k, v in payload.items()
            if k not in (TRACE_PARENT_KEY, TRACE_STATE_KEY)
        },
        "traceparent": payload.get(TRACE_PARENT_KEY),
        "tracestate": payload.get(TRACE_STATE_KEY),
    }


def deliver_outbox(db, *, batch: int = 100, consumers: list[str] | None = None) -> int:
    """Copy pending outbox events into consumer inboxes (at-least-once).

    Rows already present in a consumer's inbox are skipped thanks to the
    ``(consumer, event_id)`` unique constraint.
    """
    if batch < 1:
        raise ValidationError("batch must be >= 1")
    events = (
        db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
            .order_by(OutboxEvent.created_at)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    delivered = 0
    for event in events:
        targets = consumers if consumers is not None else local_consumers_for(event.event_type)
        inserted = sum(1 for consumer in targets if _insert_inbox(db, consumer, event.event_id))
        if inserted or not targets:
            event.status = OutboxStatus.DISPATCHED
            delivered += 1
    db.flush()
    return delivered


def process_outbox(db, *, consumer: str, batch: int = 100) -> int:
    """Deliver pending inbox events to a consumer's in-process handler.

    At-least-once semantics: if the handler raises, the inbox row is marked
    FAILED and processing continues with the next row (the failure is logged).
    Handlers must be idempotent because a crashed batch may be redelivered.
    """
    if batch < 1:
        raise ValidationError("batch must be >= 1")
    rows = (
        db.execute(
            select(InboxEvent)
            .where(InboxEvent.consumer == consumer, InboxEvent.status == InboxStatus.PENDING)
            .order_by(InboxEvent.received_at)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    handler = CONSUMER_HANDLERS.get(consumer)
    processed = 0
    for row in rows:
        event = db.get(OutboxEvent, row.event_id)
        if handler is not None and event is not None:
            try:
                handler(event)
                row.status = InboxStatus.PROCESSED
            except Exception:
                logger.exception(
                    "consumer_handler_failed",
                    consumer=consumer,
                    event_id=str(row.event_id),
                )
                row.status = InboxStatus.FAILED
        else:
            row.status = InboxStatus.PROCESSED
        processed += 1
    db.flush()
    return processed


# ---------------------------------------------------------------------------
# Inbox relay (P7 二.2): reliable claim / lease / retry / dead-letter.
# ---------------------------------------------------------------------------


def exponential_backoff_seconds(
    attempts: int,
    *,
    base: float = 1.0,
    cap: float = MAX_BACKOFF_SECONDS,
) -> float:
    """Exponential backoff after ``attempts`` failures: base * 2^(attempts-1), capped."""
    if attempts < 1:
        return 0.0
    return min(cap, base * (2 ** (attempts - 1)))


def claim_inbox_batch(
    db,
    *,
    consumer: str,
    batch: int = DEFAULT_RELAY_BATCH,
    lease_seconds: int = DEFAULT_RELAY_LEASE_SECONDS,
) -> list[InboxEvent]:
    """Atomically claim a batch of actionable inbox rows for ``consumer``.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` (PostgreSQL) so concurrent
    workers never claim the same row.  Claimed rows move to ``processing``
    with a lease; the caller commits and executes the handler *outside* this
    transaction, then marks ``processed`` / retries / dead-letters.
    """
    if batch < 1:
        raise ValidationError("batch must be >= 1")
    now = utc_now()
    stmt = (
        select(InboxEvent)
        .where(
            InboxEvent.consumer == consumer,
            InboxEvent.status == InboxStatus.PENDING,
            or_(
                InboxEvent.next_attempt_at.is_(None),
                InboxEvent.next_attempt_at <= now,
            ),
        )
        .order_by(InboxEvent.received_at, InboxEvent.id)
        .limit(batch)
        # SQLAlchemy compiles this away on SQLite (no-op) and emits
        # ``FOR UPDATE SKIP LOCKED`` on PostgreSQL.
        .with_for_update(skip_locked=True)
    )
    rows = list(db.execute(stmt).scalars().all())
    lease_until = now + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = InboxStatus.PROCESSING
        row.lease_until = lease_until
    db.commit()
    return rows


def recover_expired_leases(db, *, consumer: str | None = None, batch: int = 100) -> int:
    """Return lease-expired ``processing`` rows to ``pending`` for reprocessing.

    A crashed worker leaves its claimed rows in ``processing``; their lease
    expires and the next worker (or this worker at startup) reclaims them.
    The workflow-id / send idempotency-key dedupe prevents duplicate side
    effects.  Lease expiry is not a handler failure, so attempts are not
    bumped here.
    """
    now = utc_now()
    stmt = (
        select(InboxEvent)
        .where(
            InboxEvent.status == InboxStatus.PROCESSING,
            InboxEvent.lease_until.is_not(None),
            InboxEvent.lease_until < now,
        )
        .order_by(InboxEvent.received_at, InboxEvent.id)
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    if consumer is not None:
        stmt = stmt.where(InboxEvent.consumer == consumer)
    rows = list(db.execute(stmt).scalars().all())
    for row in rows:
        row.status = InboxStatus.PENDING
        row.lease_until = None
        row.last_error = "lease expired; reclaimed for reprocessing"
    db.commit()
    return len(rows)


@dataclass
class RelayStats:
    """Aggregate of one relay batch pass."""

    claimed: int = 0
    processed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_handled(self) -> int:
        return self.processed + self.retried + self.dead_lettered


def mark_inbox_processed(db, *, event_id: uuid.UUID) -> None:
    """Transactionally mark a claimed inbox row ``processed``."""
    row = db.get(InboxEvent, event_id)
    if row is None:
        return
    row.status = InboxStatus.PROCESSED
    row.lease_until = None
    row.processed_at = utc_now()
    db.commit()


def mark_inbox_retry(db, *, event_id: uuid.UUID, error: str, max_attempts: int) -> bool:
    """Register a failure with exponential backoff; dead-letter when exhausted.

    Returns ``True`` when the row was terminal ``failed`` (dead-lettered),
    ``False`` when it is scheduled for retry.
    """
    row = db.get(InboxEvent, event_id)
    if row is None:
        return False
    attempts = row.attempts + 1
    row.last_error = error[:2000]
    if attempts >= max_attempts:
        row.status = InboxStatus.FAILED
        row.lease_until = None
        row.attempts = attempts
        db.commit()
        return True
    row.status = InboxStatus.PENDING
    row.attempts = attempts
    row.next_attempt_at = utc_now() + timedelta(seconds=exponential_backoff_seconds(attempts))
    row.lease_until = None
    db.commit()
    return False


def relay_inbox_batch(
    db,
    *,
    consumer: str,
    dispatch: Callable[[OutboxEvent], Any],
    batch: int = DEFAULT_RELAY_BATCH,
    lease_seconds: int = DEFAULT_RELAY_LEASE_SECONDS,
    max_attempts: int = DEFAULT_RELAY_MAX_ATTEMPTS,
) -> RelayStats:
    """Claim a batch, run ``dispatch(event)`` outside the transaction and
    mark each row processed / retried / dead-lettered.

    At-least-once semantics: a crash between the claim and the processed mark
    re-runs the handler after the lease expires; deterministic workflow ids
    and DBOS send idempotency keys keep the re-run idempotent.
    """
    rows = claim_inbox_batch(
        db,
        consumer=consumer,
        batch=batch,
        lease_seconds=lease_seconds,
    )
    stats = RelayStats(claimed=len(rows))
    for row in rows:
        event = db.get(OutboxEvent, row.event_id)
        if event is None:
            # No outbox payload (already garbage-collected): nothing to run.
            mark_inbox_processed(db, event_id=row.id)
            stats.processed += 1
            continue
        try:
            dispatch(event)
        except Exception as exc:  # noqa: BLE001 - relay must survive handler errors
            logger.exception(
                "inbox_handler_failed",
                consumer=consumer,
                event_id=str(row.event_id),
                event_type=event.event_type,
            )
            terminal = mark_inbox_retry(
                db,
                event_id=row.id,
                error=str(exc),
                max_attempts=max_attempts,
            )
            if terminal:
                stats.dead_lettered += 1
                stats.errors.append(f"{event.event_type}: {exc}")
            else:
                stats.retried += 1
            continue
        mark_inbox_processed(db, event_id=row.id)
        stats.processed += 1
    return stats


__all__ = [
    "CONSUMER_HANDLERS",
    "DEFAULT_RELAY_BATCH",
    "DEFAULT_RELAY_LEASE_SECONDS",
    "DEFAULT_RELAY_MAX_ATTEMPTS",
    "PRODUCERS",
    "RelayStats",
    "TRACE_PARENT_KEY",
    "TRACE_STATE_KEY",
    "claim_inbox_batch",
    "deliver_outbox",
    "emit_event",
    "envelope_for",
    "exponential_backoff_seconds",
    "local_consumers_for",
    "mark_inbox_processed",
    "mark_inbox_retry",
    "process_outbox",
    "recover_expired_leases",
    "relay_inbox_batch",
    "register_consumer",
    "register_local_consumer",
]
