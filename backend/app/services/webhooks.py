"""Shopify webhook ingestion: dedup, vaulted raw storage, v2 domain events.

Webhooks are deduplicated by their ``X-Shopify-Webhook-Id`` using an inbox row
with consumer ``shopify-webhook``.  The raw body is encrypted into the
:class:`SensitivePayload` vault (purpose ``shopify_webhook``) under the same
30-day retention as every other sensitive payload (docs/architecture.md 6.1);
the :class:`Projection` row (owner ``shopify_webhook``) only keeps a vault
reference plus non-sensitive metadata -- never the body or any PII.

Recognised topics emit the corresponding minimal domain event (stable
references only, no raw payload expansion) and create a DBOS v2
:class:`WorkflowRun` (``orchestration_engine='dbos'``, ``workflow_version=2``)
with a minimal input.  A ``workflow.accepted`` inbox event lets the worker
relay start the deterministic v2 definition with ``SetWorkflowID`` -- the
legacy v1 slice is no longer used for webhooks.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.messaging import InboxEvent
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.projections import Projection
from app.models.returns import ReturnCase, ReturnStatus
from app.models.sensitive_payload import SensitivePayload
from app.models.workflow import WorkflowRun, WorkflowRunStatus
from app.services.outbox_inbox import emit_event
from app.services.privacy import hmac_ref, store_sensitive_payload

logger = get_logger("commerce.webhooks")

SHOPIFY_WEBHOOK_CONSUMER = "shopify-webhook"
PROJECTION_OWNER = "shopify_webhook"
WEBHOOK_VAULT_PURPOSE = "shopify_webhook"
WEBHOOK_VAULT_OWNER = "shopify_webhook"
DBOS_WORKFLOW_VERSION = 2
DBOS_ORCHESTRATION_ENGINE = "dbos"
WORKFLOW_ACCEPTED_CONSUMER = "worker"

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
        email = str(customer.get("email") or "") or None
        # Plan 5.2 / data-ownership §5: never persist a plaintext email or
        # shipping address in a business column. The customer ref column
        # carries a pseudonymous HMAC marker; plaintext lives only in the
        # encrypted sensitive-payload vault (30-day retention, tombstone).
        customer_ref = hmac_ref(email) if email else None
        shipping = payload.get("shipping_address")
        if isinstance(shipping, dict) and shipping.get("address1"):
            vault = store_sensitive_payload(
                db,
                purpose="shopify_shipping_address",
                classification="PII",
                owner="shopify_webhook",
                source_type="sales_order",
                source_id=str(payload.get("id") or ""),
                plaintext=json.dumps(shipping, ensure_ascii=False),
            )
            shipping = {"sensitivePayloadId": str(vault.id)}
        order = SalesOrder(
            order_ref=payload.get("name") or f"SHOPIFY-{payload.get('id') or uuid7()}",
            shopify_order_id=str(payload["id"]) if payload.get("id") else None,
            customer_ref=customer_ref,
            status=SalesOrderStatus.RECEIVED,
            currency=str(payload.get("currency") or "CNY"),
            total=Decimal(str(payload.get("total_price") or "0")),
            items=payload.get("line_items") or [],
            shipping=shipping,
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


def _minimal_event_payload(
    *,
    webhook_id: str,
    topic: str,
    aggregate_id: str,
    vault_id: str,
) -> dict[str, Any]:
    """Minimal domain-event payload: stable refs only, never raw webhook PII."""
    return {
        "webhook_id": webhook_id,
        "topic": topic,
        "entity_id": aggregate_id,
        "sensitivePayloadId": vault_id,
    }


def _minimal_run_input(
    *,
    topic: str,
    payload: dict[str, Any],
    webhook_id: str,
    entity_id: str,
    vault_id: str,
) -> dict[str, Any]:
    """Minimal v2 run input: stable refs + non-sensitive business fields.

    The full raw body lives only in the encrypted vault; domain events and
    workflow inputs never carry plaintext email / shipping / line-item data.
    """
    base = {
        "webhook_id": webhook_id,
        "topic": topic,
        "entity_id": entity_id,
        "sensitivePayloadId": vault_id,
    }
    if topic == "orders/create":
        shopify_id = payload.get("id")
        return {
            **base,
            "shopify_order_id": str(shopify_id) if shopify_id is not None else None,
            "order_ref": str(payload.get("name") or "") or None,
        }
    if topic == "refunds/create":
        shopify_id = payload.get("order_id")
        return {
            **base,
            "case_id": entity_id,
            "shopify_order_id": str(shopify_id) if shopify_id is not None else None,
            "refund_amount": (
                str(payload.get("amount")) if payload.get("amount") is not None else None
            ),
            "currency": str(payload.get("currency") or "CNY"),
        }
    return base


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

    # Raw webhook body -> unified vault (Fernet, 30-day retention; the same
    # cleanup job that clears shipping/customer payloads covers it).
    vault = store_sensitive_payload(
        db,
        purpose=WEBHOOK_VAULT_PURPOSE,
        classification="PII",
        owner=WEBHOOK_VAULT_OWNER,
        source_type="webhook",
        source_id=webhook_id,
        plaintext=raw_body.decode("utf-8", errors="replace"),
    )
    projection = Projection(
        owner=PROJECTION_OWNER,
        source=topic,
        external_id=webhook_id,
        observed_at=utc_now(),
        payload={
            "vaultId": str(vault.id),
            "headers": _safe_headers(headers),
            "topic": topic,
        },
    )
    db.add(projection)
    try:
        db.flush()
    except IntegrityError:
        # Duplicate projection (already ingested): reuse the existing vault
        # row (one encrypted copy per webhook) and update metadata in place.
        row = db.execute(
            select(Projection).where(
                Projection.owner == PROJECTION_OWNER,
                Projection.source == topic,
                Projection.external_id == webhook_id,
            )
        ).scalar_one()
        existing_vault = (
            db.execute(
                select(SensitivePayload).where(
                    SensitivePayload.purpose == WEBHOOK_VAULT_PURPOSE,
                    SensitivePayload.source_id == webhook_id,
                )
            )
            .scalars()
            .first()
        )
        if existing_vault is not None:
            existing_vault.ciphertext = vault.ciphertext
            existing_vault.expires_at = vault.expires_at
            db.delete(vault)
            vault = existing_vault
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

    # Minimal domain event: stable refs + vault reference only (no raw body,
    # no plaintext email/shipping/line items).
    event = emit_event(
        db,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=correlation_id,
        producer=producer,
        payload=_minimal_event_payload(
            webhook_id=webhook_id,
            topic=topic,
            aggregate_id=aggregate_id,
            vault_id=str(vault.id),
        ),
    )

    run_id: str | None = None
    if workflow_type is not None:
        run = WorkflowRun(
            workflow_type=workflow_type,
            workflow_version=DBOS_WORKFLOW_VERSION,
            orchestration_engine=DBOS_ORCHESTRATION_ENGINE,
            status=WorkflowRunStatus.ACCEPTED,
            correlation_id=correlation_id,
            input_json=_minimal_run_input(
                topic=topic,
                payload=payload,
                webhook_id=webhook_id,
                entity_id=aggregate_id,
                vault_id=str(vault.id),
            ),
        )
        db.add(run)
        db.flush()
        run_id = str(run.id)
        emit_event(
            db,
            event_type="workflow.accepted",
            aggregate_type="workflow",
            aggregate_id=run_id,
            correlation_id=correlation_id,
            producer="workflow",
            payload={
                "workflow_id": run_id,
                "workflow_type": workflow_type,
                "workflow_version": DBOS_WORKFLOW_VERSION,
                "correlation_id": correlation_id,
            },
            consumers=[WORKFLOW_ACCEPTED_CONSUMER],
        )

    return {
        "received": True,
        "deduplicated": False,
        "event_type": event_type,
        "event_id": str(event.event_id),
        "aggregate_id": aggregate_id,
        "workflow_id": run_id,
        "workflow_version": DBOS_WORKFLOW_VERSION if run_id is not None else None,
    }


__all__ = ["SHOPIFY_WEBHOOK_CONSUMER", "TOPIC_EVENT_MAP", "ingest_shopify_webhook"]
