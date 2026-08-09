"""Outbox/inbox pipeline: emission, delivery, dedup, handler failure."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.messaging import (
    InboxEvent,
    InboxStatus,
    OutboxEvent,
    OutboxStatus,
)
from app.services.outbox_inbox import (
    deliver_outbox,
    emit_event,
    process_outbox,
    register_consumer,
)


def _emit(db, *, event_type="order.received", event_id=None, consumers=None, producer="order"):
    return emit_event(
        db,
        event_id=event_id,
        event_type=event_type,
        aggregate_type="sales_order",
        aggregate_id=str(uuid.uuid4()),
        correlation_id="corr-1",
        producer=producer,
        payload={"hello": "world"},
        consumers=consumers,
    )


def _count(db, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return db.execute(stmt).scalar_one()


def test_emit_event_writes_pending_outbox(clean_outbox_registries, db) -> None:
    event = _emit(db, event_type="order.received")
    assert event.status == OutboxStatus.PENDING
    assert _count(db, OutboxEvent) == 1
    assert _count(db, InboxEvent) == 0  # no consumers registered yet


def test_process_outbox_delivers_once_per_consumer(clean_outbox_registries, db) -> None:
    handled: list[uuid.UUID] = []
    register_consumer("consumer-a", lambda e: handled.append(e.event_id))
    register_consumer("consumer-b", lambda e: handled.append(e.event_id))

    event = _emit(db, event_type="order.received", consumers=["consumer-a", "consumer-b"])
    assert _count(db, InboxEvent) == 2

    assert process_outbox(db, consumer="consumer-a") == 1
    assert process_outbox(db, consumer="consumer-b") == 1
    assert handled == [event.event_id, event.event_id]

    rows = db.execute(select(InboxEvent)).scalars().all()
    assert all(row.status == InboxStatus.PROCESSED for row in rows)
    # A second pass has nothing left to deliver.
    assert process_outbox(db, consumer="consumer-a") == 0
    assert process_outbox(db, consumer="consumer-b") == 0


def test_replaying_same_event_id_to_same_consumer_deduplicates(clean_outbox_registries, db) -> None:
    event_id = uuid.uuid4()
    # Emitted without routing so the outbox copy is pending for delivery.
    _emit(db, event_id=event_id)
    assert deliver_outbox(db, consumers=["consumer-c"]) == 1
    assert _count(db, InboxEvent, InboxEvent.consumer == "consumer-c") == 1

    # Simulate at-least-once redelivery: the same outbox row is delivered
    # again; the (consumer, event_id) unique constraint must skip the copy.
    event = db.get(OutboxEvent, event_id)
    event.status = OutboxStatus.PENDING
    assert deliver_outbox(db, consumers=["consumer-c"]) == 0
    assert _count(db, InboxEvent, InboxEvent.consumer == "consumer-c") == 1


def test_handler_failure_marks_inbox_failed_without_crash(clean_outbox_registries, db) -> None:
    def _boom(_event) -> None:
        raise RuntimeError("consumer exploded")

    register_consumer("consumer-fail", _boom)
    _emit(db, event_type="order.received", consumers=["consumer-fail"])

    processed = process_outbox(db, consumer="consumer-fail")
    assert processed == 1
    row = db.execute(select(InboxEvent)).scalar_one()
    assert row.status == InboxStatus.FAILED
    # Processing continues; a second run finds nothing pending.
    assert process_outbox(db, consumer="consumer-fail") == 0


def test_unregistered_consumer_marks_processed(clean_outbox_registries, db) -> None:
    _emit(db, event_type="order.received", consumers=["consumer-none"])
    assert process_outbox(db, consumer="consumer-none") == 1
    row = db.execute(select(InboxEvent)).scalar_one()
    assert row.status == InboxStatus.PROCESSED


def test_deliver_outbox_copies_pending_events_to_new_consumers(clean_outbox_registries, db) -> None:
    _emit(db, event_type="order.received")
    _emit(db, event_type="order.received")
    assert _count(db, InboxEvent) == 0
    assert deliver_outbox(db, consumers=["late-consumer"]) == 2
    assert _count(db, InboxEvent, InboxEvent.consumer == "late-consumer") == 2
    events = db.execute(select(OutboxEvent)).scalars().all()
    assert all(e.status == OutboxStatus.DISPATCHED for e in events)


def test_unknown_event_type_and_producer_rejected(db) -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        emit_event(
            db,
            event_type="no.such.event",
            aggregate_type="x",
            aggregate_id="1",
            producer="order",
        )
    with pytest.raises(ValidationError):
        _emit(db, event_type="order.received", producer="not-a-producer")
    with pytest.raises(ValidationError):
        # Producer may not emit events from a foreign domain.
        _emit(db, event_type="catalog.revision_drafted", producer="order")


def test_deliver_outbox_batch_validation(db) -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        deliver_outbox(db, batch=0)
    with pytest.raises(ValidationError):
        process_outbox(db, consumer="x", batch=0)
