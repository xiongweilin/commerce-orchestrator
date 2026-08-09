"""Shopify webhook ingestion: dedup, encrypted raw storage, domain events.

Webhooks are deduplicated by their ``X-Shopify-Webhook-Id`` using an inbox row
with consumer ``shopify-webhook``.  The encrypted raw body is stored on a
:class:`Projection` row (owner ``shopify_webhook``) -- no new table is needed.
Each recognised topic then emits the corresponding domain event and starts the
domain workflow state machine (a :class:`WorkflowRun`).
"""

from __future__ import annotations

import base64
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.security import encrypt_payload
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.messaging import InboxEvent
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.projections import Projection
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import WorkflowRun, WorkflowRunStatus
from app.services.outbox_inbox import emit_event

logger = get_logger("commerce.webhooks")

SHOPIFY_WEBHOOK_CONSUMER = "shopify-webhook"
PROJECTION_OWNER = "shopify_webhook"

# topic -> (event_type, aggregate_type, producer, workflow_type)
TOPIC_EVENT_MAP: dict[str, tuple[str, str, str, str | None]] = {
    "orders/create": ("order.received", "sales_order", "shopify_adapter", "order-to-cash"),
    "refunds/create": (
        "return.case_requested",
        "return_case",
        "shopify_adapter",
        "return-to-refund",
    ),
    "order_transactions/create": ("order.in_payment", "sales_order", "shopify_adapter", None),
    "fulfillments/create": ("order.shipped", "sales_order", "shopify_adapter", None),
    "products/create": ("catalog.revision_drafted", "catalog_revision", "shopify_adapter", None),
    "products/update": ("catalog.revision_drafted", "catalog_revision", "shopify_adapter", None),
}


def _safe_headers(headers: dict[str, str] | None) -> dict[str, str]:
    headers = headers or {}
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"x-shopify-hmac-sha256", "authorization", "cookie"}
    }


def _entity_id_for(topic: str, payload: dict[str, Any], webhook_id: str) -> str:
    raw = payload.get("id") or payload.get("order_id") or payload.get("name")
    if raw is not None:
        return str(raw)
    return webhook_id


def _start_domain_entity(
    db,
    *,
    topic: str,
    payload: dict[str, Any],
    correlation_id: str,
) -> tuple[str | None, str | None]:
    """Create the domain entity at its initial state for known topics.

    Returns ``(entity_id, run_id)``; both are None for unrecognised topics.
    """
    if topic == "orders/create":
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        order = SalesOrder(
            order_ref=payload.get("name") or f"SHOPIFY-{payload.get('id') or uuid7()}",
            shopify_order_id=str(payload["id"]) if payload.get("id") else None,
            customer_ref=str(customer.get("email") or "") or None,
            status=SalesOrderStatus.RECEIVED,
            currency=str(payload.get("currency") or "CNY"),
            total=Decimal(str(payload.get("total_price") or "0")),
            items=payload.get("line_items") or [],
            shipping=payload.get("shipping_address"),
        )
        db.add(order)
        db.flush()
        return str(order.id), None
    if topic == "refunds/create":
        case = ReturnCase(
            return_ref=payload.get("id") and f"RET-{payload['id']}" or f"RET-{uuid7()}",
            shopify_order_id=str(payload.get("order_id") or ""),
            customer_ref=str(payload.get("customer_ref") or ""),
            reason=payload.get("reason") or "shopify refund webhook",
            status=ReturnStatus.REQUESTED,
            refund_amount=(
                Decimal(str(payload.get("amount") or "0"))
                if payload.get("amount") is not None
                else None
            ),
            currency=str(payload.get("currency") or "CNY"),
        )
        db.add(case)
        db.flush()
        return str(case.id), None
    return None, None


def ingest_shopify_webhook(
    db,
    *,
    webhook_id: str,
    topic: str,
    raw_body: bytes,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ingest a Shopify webhook: dedup, encrypt+store, emit domain event.

    Returns ``{"received": True, "deduplicated": bool, ...}``.  ``webhook_id``
    must be a UUID string (the ``X-Shopify-Webhook-Id`` header value).
    """
    try:
        webhook_uuid = uuid.UUID(webhook_id)
    except ValueError as exc:
        raise ValidationError(f"invalid webhook id: {webhook_id}") from exc
    if not topic:
        raise ValidationError("topic is required")

    existing = db.execute(
        select(InboxEvent).where(
            InboxEvent.consumer == SHOPIFY_WEBHOOK_CONSUMER,
            InboxEvent.event_id == webhook_uuid,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"received": True, "deduplicated": True, "event_type": None, "event_id": None}

    with db.begin_nested():
        db.add(InboxEvent(consumer=SHOPIFY_WEBHOOK_CONSUMER, event_id=webhook_uuid))

    encrypted = base64.b64encode(encrypt_payload(raw_body)).decode("ascii")
    projection = Projection(
        owner=PROJECTION_OWNER,
        source=topic,
        external_id=webhook_id,
        observed_at=utc_now(),
        payload={"enc": encrypted, "headers": _safe_headers(headers), "topic": topic},
    )
    db.add(projection)
    try:
        db.flush()
    except IntegrityError:
        # Duplicate projection (already ingested): update in place.
        row = db.execute(
            select(Projection).where(
                Projection.owner == PROJECTION_OWNER,
                Projection.source == topic,
                Projection.external_id == webhook_id,
            )
        ).scalar_one()
        row.payload = projection.payload
        row.observed_at = utc_now()

    mapping = TOPIC_EVENT_MAP.get(topic)
    if mapping is None:
        return {
            "received": True,
            "deduplicated": False,
            "event_type": None,
            "event_id": None,
            "note": f"no domain mapping for topic {topic!r}",
        }

    event_type, aggregate_type, producer, workflow_type = mapping
    correlation_id = str(uuid7())
    entity_id, _run_id = _start_domain_entity(
        db, topic=topic, payload=payload, correlation_id=correlation_id
    )
    aggregate_id = entity_id or _entity_id_for(topic, payload, webhook_id)

    event = emit_event(
        db,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=correlation_id,
        producer=producer,
        payload={"webhook_id": webhook_id, "topic": topic, **payload},
        consumers=["worker"],
    )

    run_id: str | None = None
    if workflow_type is not None:
        run = WorkflowRun(
            workflow_type=workflow_type,
            workflow_version=1,
            status=WorkflowRunStatus.RUNNING,
            correlation_id=correlation_id,
            input_json={"webhook_id": webhook_id, "topic": topic, **payload},
        )
        db.add(run)
        db.flush()
        run_id = str(run.id)

    return {
        "received": True,
        "deduplicated": False,
        "event_type": event_type,
        "event_id": str(event.event_id),
        "aggregate_id": aggregate_id,
        "workflow_id": run_id,
    }


__all__ = ["SHOPIFY_WEBHOOK_CONSUMER", "TOPIC_EVENT_MAP", "ingest_shopify_webhook"]
