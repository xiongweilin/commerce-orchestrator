"""Outbox / inbox event pipeline.

Every domain event is written to the outbox (``outbox_event``) and copied into
the inbox (``inbox_event``) of every local consumer.  Delivery is
at-least-once: consumers must be idempotent and deduplicate on
``(consumer, event_id)`` (enforced by a unique constraint on the table).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.messaging import InboxEvent, InboxStatus, OutboxEvent, OutboxStatus
from app.schemas.events import EVENT_TYPES

logger = get_logger("commerce.outbox")

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
) -> OutboxEvent:
    """Write an outbox event and copy it into local consumer inboxes."""
    if event_type not in EVENT_TYPES:
        raise ValidationError(f"unknown event type: {event_type}")
    if not aggregate_type or not aggregate_id:
        raise ValidationError("aggregate_type and aggregate_id are required")
    _validate_producer(event_type, producer)

    event_id = event_id or uuid7()
    occurred_at = occurred_at or utc_now()
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
        payload=payload or {},
    )
    db.add(event)
    targets = consumers if consumers is not None else local_consumers_for(event_type)
    for consumer in targets:
        _insert_inbox(db, consumer, event_id)
    db.flush()
    return event


def envelope_for(event: OutboxEvent) -> dict[str, Any]:
    """Return the wire envelope for an outbox event (JSON-safe dict)."""
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
        "payload": event.payload,
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


__all__ = [
    "CONSUMER_HANDLERS",
    "PRODUCERS",
    "deliver_outbox",
    "emit_event",
    "envelope_for",
    "local_consumers_for",
    "process_outbox",
    "register_consumer",
    "register_local_consumer",
]
