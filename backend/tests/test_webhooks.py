"""Shopify webhook ingestion: dedup, encrypted raw storage, domain events."""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.core.security import decrypt_payload
from app.models.messaging import InboxEvent, OutboxEvent
from app.models.order import SalesOrder
from app.models.projections import Projection
from app.models.returns import ReturnCase
from app.services.webhooks import (
    SHOPIFY_WEBHOOK_CONSUMER,
    ingest_shopify_webhook,
)


def _webhook_id() -> str:
    return str(uuid.uuid4())


def _raw_order_body() -> bytes:
    return json.dumps(
        {
            "id": 1001,
            "name": "#S1001",
            "email": "buyer@example.com",
            "currency": "USD",
            "total_price": "25.00",
            "line_items": [{"id": 1, "sku": "SKU-1", "quantity": 2}],
        }
    ).encode("utf-8")


def test_orders_create_emits_order_received_and_creates_order(db) -> None:
    raw = _raw_order_body()
    payload = json.loads(raw)
    result = ingest_shopify_webhook(
        db,
        webhook_id=_webhook_id(),
        topic="orders/create",
        raw_body=raw,
        payload=payload,
    )
    assert result["received"] is True
    assert result["deduplicated"] is False
    assert result["event_type"] == "order.received"

    events = db.execute(select(OutboxEvent)).scalars().all()
    assert [e.event_type for e in events] == ["order.received"]
    assert events[0].aggregate_type == "sales_order"
    assert events[0].producer == "shopify_adapter"

    order = db.execute(select(SalesOrder)).scalar_one()
    assert order.shopify_order_id == "1001"
    assert order.status.value == "received"
    assert str(order.total) == "25.00"


def test_refunds_create_emits_return_case_requested(db) -> None:
    raw = json.dumps(
        {"id": 2002, "order_id": 1001, "amount": "12.50", "reason": "not as described"}
    ).encode("utf-8")
    result = ingest_shopify_webhook(
        db,
        webhook_id=_webhook_id(),
        topic="refunds/create",
        raw_body=raw,
        payload=json.loads(raw),
    )
    assert result["event_type"] == "return.case_requested"
    case = db.execute(select(ReturnCase)).scalar_one()
    assert case.status.value == "requested"
    assert case.shopify_order_id == "1001"
    assert str(case.refund_amount) == "12.50"


def test_same_webhook_id_is_deduplicated(db) -> None:
    webhook_id = _webhook_id()
    raw = _raw_order_body()
    payload = json.loads(raw)
    first = ingest_shopify_webhook(
        db,
        webhook_id=webhook_id,
        topic="orders/create",
        raw_body=raw,
        payload=payload,
    )
    second = ingest_shopify_webhook(
        db,
        webhook_id=webhook_id,
        topic="orders/create",
        raw_body=raw,
        payload=payload,
    )
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["event_type"] is None

    outbox_count = db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one()
    assert outbox_count == 1
    inbox_count = db.execute(
        select(func.count())
        .select_from(InboxEvent)
        .where(InboxEvent.consumer == SHOPIFY_WEBHOOK_CONSUMER)
    ).scalar_one()
    assert inbox_count == 1
    assert len(db.execute(select(SalesOrder)).scalars().all()) == 1


def test_raw_body_stored_encrypted(db) -> None:
    raw = _raw_order_body()
    webhook_id = _webhook_id()
    ingest_shopify_webhook(
        db,
        webhook_id=webhook_id,
        topic="orders/create",
        raw_body=raw,
        payload=json.loads(raw),
    )
    projection = db.execute(select(Projection)).scalar_one()
    assert projection.owner == "shopify_webhook"
    assert projection.external_id == webhook_id
    encrypted = base64.b64decode(projection.payload["enc"])
    assert decrypt_payload(encrypted) == raw
    # The plaintext must not be recoverable from the stored payload.
    stored = json.dumps(projection.payload)
    assert raw.decode("utf-8") not in stored
    assert "buyer@example.com" not in stored


def test_invalid_webhook_id_and_missing_topic_rejected(db) -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        ingest_shopify_webhook(
            db,
            webhook_id="not-a-uuid",
            topic="orders/create",
            raw_body=b"{}",
            payload={},
        )
    with pytest.raises(ValidationError):
        ingest_shopify_webhook(
            db,
            webhook_id=_webhook_id(),
            topic="",
            raw_body=b"{}",
            payload={},
        )


def test_unmapped_topic_is_received_without_event(db) -> None:
    result = ingest_shopify_webhook(
        db,
        webhook_id=_webhook_id(),
        topic="customers/create",
        raw_body=b"{}",
        payload={},
    )
    assert result["received"] is True
    assert result["event_type"] is None
    assert result["note"]
