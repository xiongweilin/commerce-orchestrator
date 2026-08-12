"""Shopify webhook ingestion: dedup, vaulted raw storage, v2 domain events."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.core.security import decrypt_payload
from app.models.messaging import InboxEvent, OutboxEvent
from app.models.order import SalesOrder
from app.models.projections import Projection
from app.models.returns import ReturnCase
from app.models.sensitive_payload import SensitivePayload
from app.models.workflow import WorkflowRun, WorkflowRunStatus
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


def _accepted_run(db) -> WorkflowRun:
    return db.execute(select(WorkflowRun)).scalar_one()


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
    assert result["workflow_version"] == 2
    assert result["workflow_id"] is not None

    events = (
        db.execute(select(OutboxEvent).order_by(OutboxEvent.occurred_at)).scalars().all()
    )
    assert [e.event_type for e in events] == ["order.received", "workflow.accepted"]
    assert events[0].aggregate_type == "sales_order"
    assert events[0].producer == "shopify_adapter"
    # The domain event carries stable refs only -- never raw webhook PII.
    assert set(events[0].payload) == {
        "webhook_id",
        "topic",
        "entity_id",
        "sensitivePayloadId",
    }
    accepted = events[1]
    assert accepted.producer == "workflow"
    assert accepted.payload["workflow_type"] == "order-to-cash"
    assert accepted.payload["workflow_version"] == 2
    assert accepted.payload["workflow_id"] == result["workflow_id"]

    order = db.execute(select(SalesOrder)).scalar_one()
    assert order.shopify_order_id == "1001"
    assert order.status.value == "received"
    assert str(order.total) == "25.00"

    # DBOS v2 run with a minimal input (no raw payload expansion).
    run = _accepted_run(db)
    assert run.workflow_type == "order-to-cash"
    assert run.workflow_version == 2
    assert run.orchestration_engine == "dbos"
    assert run.status == WorkflowRunStatus.ACCEPTED
    assert run.input_json["shopify_order_id"] == "1001"
    assert run.input_json["entity_id"] == str(order.id)
    blob = json.dumps(run.input_json)
    assert "buyer@example.com" not in blob
    assert "line_items" not in blob
    assert "email" not in blob


def test_refunds_create_emits_return_case_requested_and_v2_run(db) -> None:
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
    assert result["workflow_version"] == 2
    case = db.execute(select(ReturnCase)).scalar_one()
    assert case.status.value == "requested"
    assert case.shopify_order_id == "1001"
    assert str(case.refund_amount) == "12.50"

    run = _accepted_run(db)
    assert run.workflow_type == "return-to-refund"
    assert run.workflow_version == 2
    assert run.orchestration_engine == "dbos"
    assert run.input_json["case_id"] == str(case.id)
    assert run.input_json["refund_amount"] == "12.50"
    assert run.input_json["sensitivePayloadId"]


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

    # One domain event + one workflow.accepted event; a single v2 run and a
    # single vault copy of the raw body.
    outbox_count = db.execute(select(func.count()).select_from(OutboxEvent)).scalar_one()
    assert outbox_count == 2
    inbox_count = db.execute(
        select(func.count())
        .select_from(InboxEvent)
        .where(InboxEvent.consumer == SHOPIFY_WEBHOOK_CONSUMER)
    ).scalar_one()
    assert inbox_count == 1
    assert len(db.execute(select(SalesOrder)).scalars().all()) == 1
    assert len(db.execute(select(WorkflowRun)).scalars().all()) == 1
    assert len(db.execute(select(SensitivePayload)).scalars().all()) == 1


def test_raw_body_stored_in_vault_not_projection(db) -> None:
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
    # Projection keeps only a vault reference + metadata (docs/architecture.md
    # 6.1); the encrypted body lives in the unified sensitive-payload vault.
    assert "enc" not in projection.payload

    vault = db.execute(select(SensitivePayload)).scalar_one()
    assert vault.purpose == "shopify_webhook"
    assert vault.source_id == webhook_id
    assert vault.expires_at is not None  # default 30-day retention
    assert decrypt_payload(vault.ciphertext.encode("ascii")) == raw
    assert projection.payload["vaultId"] == str(vault.id)

    # The plaintext must not be recoverable from projection metadata or the
    # ciphertext column.
    stored = json.dumps(projection.payload)
    assert raw.decode("utf-8") not in stored
    assert "buyer@example.com" not in stored
    assert "buyer@example.com" not in vault.ciphertext


def test_webhook_pii_never_in_events_or_run_input(db) -> None:
    raw = json.dumps(
        {
            "id": 3003,
            "name": "#S3003",
            "email": "buyer@example.com",
            "currency": "USD",
            "total_price": "25.00",
            "shipping_address": {
                "address1": "Secret St 1",
                "city": "Shanghai",
                "zip": "200000",
            },
            "customer": {"email": "buyer@example.com", "first_name": "Ada"},
            "line_items": [{"id": 1, "sku": "SKU-1", "quantity": 2}],
        }
    ).encode("utf-8")
    ingest_shopify_webhook(
        db,
        webhook_id=_webhook_id(),
        topic="orders/create",
        raw_body=raw,
        payload=json.loads(raw),
    )

    order = db.execute(select(SalesOrder)).scalar_one()
    # Email -> HMAC pseudonymous marker; shipping address -> vault reference.
    assert order.customer_ref.startswith("pii:")
    assert "sensitivePayloadId" in order.shipping
    assert "Secret St" not in json.dumps(order.shipping)

    for event in db.execute(select(OutboxEvent)).scalars().all():
        blob = json.dumps(event.payload or {})
        assert "buyer@example.com" not in blob
        assert "Secret St" not in blob
        assert "SKU-1" not in blob
        assert "Ada" not in blob

    run = _accepted_run(db)
    blob = json.dumps(run.input_json or {})
    assert "buyer@example.com" not in blob
    assert "Secret St" not in blob
    assert "SKU-1" not in blob
    assert "Ada" not in blob


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
    # Even unmapped raw bodies are retained in the vault.
    assert len(db.execute(select(SensitivePayload)).scalars().all()) == 1
