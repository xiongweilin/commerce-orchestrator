"""Shared event/effect vocabulary and the outbox envelope.

The constants in this module are the single source of truth for event types,
effect operations, feedback types and roles across the whole repository —
models, workflows, connectors and the API must import them rather than
redefine the strings.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import Rfc3339Datetime

FEEDBACK_EVENTS = (
    "feedback.observed",
    "feedback.clustered",
    "feedback.candidate_created",
    "feedback.reviewed",
    "feedback.promoted",
    "feedback.rejected",
)

CATALOG_EVENTS = (
    "catalog.revision_drafted",
    "catalog.normalized",
    "catalog.validated",
    "catalog.approved",
    "catalog.official",
    "catalog.superseded",
)

LISTING_EVENTS = (
    "listing.publishing",
    "listing.published",
    "listing.publish_failed",
    "listing.suspended",
    "listing.retired",
)

ORDER_EVENTS = (
    "order.received",
    "order.validated",
    "order.accepted",
    "order.odo_drafted",
    "order.confirmed",
    "order.reserved",
    "order.picking",
    "order.shipped",
    "order.invoiced",
    "order.in_payment",
    "order.reconciled",
    "order.closed",
)

PROCUREMENT_EVENTS = (
    "procurement.demand_detected",
    "procurement.rfq_drafted",
    "procurement.pending_approval",
    "procurement.po_confirmed",
    "procurement.partially_received",
    "procurement.received",
    "procurement.bill_posted",
    "procurement.in_payment",
    "procurement.reconciled",
    "procurement.closed",
)

RETURN_EVENTS = (
    "return.case_requested",
    "return.eligibility_reviewed",
    "return.authorized",
    "return.goods_received",
    "return.inspected",
    "return.disposition_approved",
    "return.credit_note_posted",
    "return.refund_pending",
    "return.refund_succeeded",
    "return.reconciled",
    "return.closed",
)

WORKFLOW_EVENTS = (
    "workflow.accepted",
    "workflow.completed",
    "workflow.failed",
    "workflow.cancelled",
)

EFFECT_EVENTS = (
    "effect.planned",
    "effect.dispatched",
    "effect.succeeded",
    "effect.failed",
    "effect.outcome_unknown",
    "effect.reconciled",
    "effect.manual_reconciliation",
)

EVENT_TYPES = frozenset(
    FEEDBACK_EVENTS
    + CATALOG_EVENTS
    + LISTING_EVENTS
    + ORDER_EVENTS
    + PROCUREMENT_EVENTS
    + RETURN_EVENTS
    + WORKFLOW_EVENTS
    + EFFECT_EVENTS
)

SHOPIFY_EFFECT_OPS = (
    "shopify.product_publish",
    "shopify.product_update",
    "shopify.fulfillment_create",
    "shopify.refund_create",
)

ODOO_EFFECT_OPS = (
    "odoo.product_create",
    "odoo.product_update",
    "odoo.sale_order_create",
    "odoo.sale_order_confirm",
    "odoo.stock_move_create",
    "odoo.picking_create",
    "odoo.picking_validate",
    "odoo.invoice_create",
    "odoo.invoice_validate",
    "odoo.credit_note_create",
    "odoo.credit_note_validate",
    "odoo.po_create",
    "odoo.po_confirm",
    "odoo.bill_create",
    "odoo.receive_transfer",
)

EFFECT_OPS = frozenset(SHOPIFY_EFFECT_OPS + ODOO_EFFECT_OPS)

FEEDBACK_TYPES = (
    "product_quality",
    "content_accuracy",
    "pricing_promotion",
    "availability",
    "payment",
    "fulfillment",
    "packaging",
    "service",
    "return_refund",
    "fraud_abuse",
    "other",
)

ROLES = (
    "catalog_owner",
    "commerce_lead",
    "finance_approver",
    "procurement_lead",
    "budget_owner",
    "warehouse_staff",
    "inventory_supervisor",
    "accountant",
    "customer_service",
    "compliance",
    "system_admin",
)


class EventEnvelope(BaseModel):
    """Outbox/inbox event envelope; field names follow the wire contract."""

    eventId: UUID
    type: str
    aggregateType: str
    aggregateId: str
    aggregateVersion: int | None = None
    occurredAt: Rfc3339Datetime
    correlationId: str | None = None
    causationId: str | None = None
    producer: str
    schemaVersion: str = "1.0"
    payload: dict[str, Any] = Field(default_factory=dict)


Envelope = EventEnvelope

__all__ = [
    "CATALOG_EVENTS",
    "EFFECT_EVENTS",
    "EFFECT_OPS",
    "EVENT_TYPES",
    "Envelope",
    "EventEnvelope",
    "FEEDBACK_EVENTS",
    "FEEDBACK_TYPES",
    "LISTING_EVENTS",
    "ODOO_EFFECT_OPS",
    "ORDER_EVENTS",
    "PROCUREMENT_EVENTS",
    "RETURN_EVENTS",
    "ROLES",
    "SHOPIFY_EFFECT_OPS",
    "WORKFLOW_EVENTS",
]
