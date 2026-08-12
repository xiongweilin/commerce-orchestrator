"""Command dispatch with idempotency, plus the v1 inline domain state machines.

``dispatch_command`` is the single entry point for write commands:

1. Idempotency lookup on ``(scope, key)`` -- same request hash replays the
   stored result; a different hash raises :class:`IdempotencyConflictError`.
2. Creates the :class:`WorkflowRun` (status ``running``).
3. Runs the domain entry for the command type synchronously inside the
   caller's transaction (v1; DBOS wrapping lives in ``app/workflows``).
4. Persists the idempotency record with the stored result.

The domain entries advance each entity's state machine to its first approval
gate and create the corresponding work item.  Approving a work item runs the
registered continuation (see :func:`register_next_step` in
``app.services.approvals``) which advances the machine to the next gate or,
for external effects, records planned effects for the worker to execute.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.errors import (
    ConflictError,
    IdempotencyConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.messaging import IdempotencyRecord
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem
from app.services.approvals import create_work_item, register_next_step
from app.services.effect_ledger import record_effect
from app.services.outbox_inbox import emit_event
from app.services.reconciliation import run_reconciliation
from app.services.state_machines import can_transition

logger = get_logger("commerce.commands")

COMMAND_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {}

# New commands are accepted as DBOS v2 workflows (orchestration_engine="dbos",
# workflow_version=2).  The legacy inline path (dispatch_command) stays as the
# compatibility adapter that finishes in-flight legacy_inline runs.
DBOS_WORKFLOW_VERSION = 2
DBOS_ORCHESTRATION_ENGINE = "dbos"
ACCEPTED_EVENT_CONSUMER = "worker"
IDEMPOTENCY_SCOPE_COMMAND = "command"

# Machine name -> entity status enum (status columns are StrEnum-backed).
_STATUS_ENUMS: dict[str, type] = {
    "CatalogRevision": CatalogRevisionStatus,
    "ListingPublication": ListingStatus,
    "SalesOrder": SalesOrderStatus,
    "ProcurementOrder": ProcurementStatus,
    "ReturnCase": ReturnStatus,
    "EffectLedgerEntry": EffectStatus,
}

# Machine name -> event type emitted when the entity enters that state.
_EVENT_BY_STATE: dict[str, dict[str, str]] = {
    "CatalogRevision": {
        "draft": "catalog.revision_drafted",
        "normalized": "catalog.normalized",
        "validated": "catalog.validated",
        "approved": "catalog.approved",
        "official": "catalog.official",
        "superseded": "catalog.superseded",
    },
    "ListingPublication": {
        "publishing": "listing.publishing",
        "active": "listing.published",
        "publish_failed": "listing.publish_failed",
        "suspended": "listing.suspended",
        "retired": "listing.retired",
    },
    "SalesOrder": {
        "received": "order.received",
        "validated": "order.validated",
        "accepted": "order.accepted",
        "odo_drafted": "order.odo_drafted",
        "confirmed": "order.confirmed",
        "reserved": "order.reserved",
        "picking": "order.picking",
        "shipped": "order.shipped",
        "invoiced": "order.invoiced",
        "in_payment": "order.in_payment",
        "reconciled": "order.reconciled",
        "closed": "order.closed",
    },
    "ProcurementOrder": {
        "demand_detected": "procurement.demand_detected",
        "rfq_draft": "procurement.rfq_drafted",
        "pending_approval": "procurement.pending_approval",
        "po_confirmed": "procurement.po_confirmed",
        "partially_received": "procurement.partially_received",
        "received": "procurement.received",
        "bill_posted": "procurement.bill_posted",
        "in_payment": "procurement.in_payment",
        "reconciled": "procurement.reconciled",
        "closed": "procurement.closed",
    },
    "ReturnCase": {
        "requested": "return.case_requested",
        "eligibility_review": "return.eligibility_reviewed",
        "authorized": "return.authorized",
        "received": "return.goods_received",
        "inspected": "return.inspected",
        "disposition_approved": "return.disposition_approved",
        "credit_note_posted": "return.credit_note_posted",
        "refund_pending": "return.refund_pending",
        "refund_succeeded": "return.refund_succeeded",
        "reconciled": "return.reconciled",
        "closed": "return.closed",
    },
}

# Machine name -> producer used for emitted domain events.
_PRODUCER_BY_MACHINE: dict[str, str] = {
    "CatalogRevision": "catalog",
    "ListingPublication": "listing",
    "SalesOrder": "order",
    "ProcurementOrder": "procurement",
    "ReturnCase": "return",
}

_AGGREGATE_TYPE_BY_MACHINE: dict[str, str] = {
    "CatalogRevision": "catalog_revision",
    "ListingPublication": "listing_publication",
    "SalesOrder": "sales_order",
    "ProcurementOrder": "procurement_order",
    "ReturnCase": "return_case",
}


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON encoding of a command payload."""
    canonical = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _uuid(value: Any, *, field: str = "id") -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"invalid {field}: {value!r}") from exc


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - surface a domain error
        raise ValidationError(f"invalid {field}: {value!r}") from exc


def advance_entity(
    db,
    entity,
    machine: str,
    to_state: str,
    *,
    correlation_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    emit: bool = True,
    actor_user_id: Any = None,
) -> str:
    """Validate and apply a legal transition, emitting the domain event.

    Raises :class:`ConflictError` when the transition is illegal.  Shared by
    the command entries and the DBOS vertical-slice finalizers.
    """
    from_state = entity.status.value
    ok, reason = can_transition(machine, from_state, to_state, context)
    if not ok:
        raise ConflictError(f"{machine} {from_state} -> {to_state}: {reason}")
    entity.status = _STATUS_ENUMS[machine](to_state)
    if emit:
        event_type = _EVENT_BY_STATE[machine].get(to_state)
        if event_type is not None:
            emit_event(
                db,
                event_type=event_type,
                aggregate_type=_AGGREGATE_TYPE_BY_MACHINE[machine],
                aggregate_id=str(entity.id),
                correlation_id=correlation_id,
                producer=_PRODUCER_BY_MACHINE[machine],
                payload={
                    "state": to_state,
                    "actor_user_id": str(actor_user_id) if actor_user_id else None,
                },
            )
    db.flush()
    return to_state


def _run_result(run: WorkflowRun, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "workflowId": str(run.id),
        "status": run.status.value,
        "statusUrl": f"/v1/workflows/{run.id}",
        **(extras or {}),
    }


def _complete_run(db, run: WorkflowRun, *, extras: dict[str, Any] | None = None) -> None:
    """Finalise a run as completed.

    For DBOS v2 runs the terminal transition is owned by the workflow driver
    (``app.workflows.definitions._complete_txn``): the v1 continuation (e.g.
    the closing gate) only records the result here and leaves the run in
    ``running`` so planned effects are executed first and outcome_unknown /
    failed can still settle on the run (plan 二.4 execution order).  Legacy
    inline runs keep the immediate completion.
    """
    if run.orchestration_engine == "dbos":
        run.result_json = _run_result(run, extras)
        db.flush()
        return
    run.status = WorkflowRunStatus.COMPLETED
    run.result_json = _run_result(run, extras)
    emit_event(
        db,
        event_type="workflow.completed",
        aggregate_type="workflow",
        aggregate_id=str(run.id),
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": str(run.id), "workflow_type": run.workflow_type},
    )


# ---------------------------------------------------------------------------
# Domain entries (v1 inline state machines)
# ---------------------------------------------------------------------------


def catalog_revision_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    sku = payload.get("sku")
    if not sku:
        raise ValidationError("sku is required")
    revision = CatalogRevision(
        candidate_id=_uuid(payload.get("candidate_id"), field="candidate_id"),
        sku=str(sku),
        title=str(payload.get("title") or sku),
        description=payload.get("description"),
        category=payload.get("category"),
        status=CatalogRevisionStatus.DRAFT,
        current=payload.get("current"),
        proposed=payload.get("proposed") or {},
    )
    db.add(revision)
    db.flush()
    emit_event(
        db,
        event_type="catalog.revision_drafted",
        aggregate_type="catalog_revision",
        aggregate_id=str(revision.id),
        correlation_id=correlation_id,
        producer="catalog",
        payload={"sku": str(sku), "state": "draft"},
    )
    for state in ("normalized", "validated", "pending_approval"):
        advance_entity(db, revision, "CatalogRevision", state, correlation_id=correlation_id)

    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve catalog revision for SKU {sku}",
        required_roles=["catalog_owner"],
        payload={
            "revision_id": str(revision.id),
            "sku": str(sku),
            "proposed_by_user_id": str(actor_user_id) if actor_user_id else None,
            "compliance_vetoable": True,
            "next_step": "approve",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"revisionId": str(revision.id), "workItemId": str(item.id)})


def listing_publication_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    sku = payload.get("sku")
    if not sku:
        raise ValidationError("sku is required")
    listing = ListingPublication(
        sku=str(sku),
        channel=str(payload.get("channel") or "shopify"),
        status=ListingStatus.DRAFT,
        payload=payload.get("payload") or {},
    )
    db.add(listing)
    db.flush()
    for state in ("validated", "pending_approval"):
        advance_entity(db, listing, "ListingPublication", state, correlation_id=correlation_id)

    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve publication of SKU {sku} on {listing.channel}",
        required_roles=["catalog_owner"],
        payload={
            "listing_id": str(listing.id),
            "sku": str(sku),
            "proposed_by_user_id": str(actor_user_id) if actor_user_id else None,
            "compliance_vetoable": True,
            "next_step": "approve",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"listingId": str(listing.id), "workItemId": str(item.id)})


def procurement_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    sku = payload.get("sku")
    supplier = payload.get("supplier")
    if not sku or not supplier:
        raise ValidationError("sku and supplier are required")
    qty = _decimal(payload.get("qty", 1), field="qty")
    unit_cost = _decimal(payload.get("unit_cost", 0), field="unit_cost")
    order = ProcurementOrder(
        sku=str(sku),
        qty=qty,
        uom=str(payload.get("uom") or "unit"),
        supplier=str(supplier),
        unit_cost=unit_cost,
        currency=str(payload.get("currency") or "CNY"),
        status=ProcurementStatus.DEMAND_DETECTED,
        created_by=_uuid(actor_user_id, field="actor_user_id"),
    )
    db.add(order)
    db.flush()
    emit_event(
        db,
        event_type="procurement.demand_detected",
        aggregate_type="procurement_order",
        aggregate_id=str(order.id),
        correlation_id=correlation_id,
        producer="procurement",
        payload={"sku": str(sku), "state": "demand_detected"},
    )
    for state in ("rfq_draft", "pending_approval"):
        advance_entity(db, order, "ProcurementOrder", state, correlation_id=correlation_id)

    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve purchase order {order.id} for SKU {sku}",
        required_roles=["budget_owner"],
        payload={
            "po_id": str(order.id),
            "sku": str(sku),
            "proposed_by_user_id": str(actor_user_id) if actor_user_id else None,
            "four_eyes_area": "po",
            "expected_total": str(qty * unit_cost),
            "next_step": "approve_po",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"poId": str(order.id), "workItemId": str(item.id)})


def return_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    customer_ref = payload.get("customer_ref")
    reason = payload.get("reason")
    if not customer_ref or not reason:
        raise ValidationError("customer_ref and reason are required")
    case = ReturnCase(
        return_ref=str(payload.get("return_ref") or f"RET-{uuid7()}"),
        shopify_order_id=(
            str(payload["shopify_order_id"]) if payload.get("shopify_order_id") else None
        ),
        order_ref=str(payload.get("order_ref")) if payload.get("order_ref") else None,
        customer_ref=str(customer_ref),
        reason=str(reason),
        status=ReturnStatus.REQUESTED,
        refund_amount=(
            _decimal(payload.get("refund_amount"), field="refund_amount")
            if payload.get("refund_amount") is not None
            else None
        ),
        currency=str(payload.get("currency") or "CNY"),
    )
    db.add(case)
    db.flush()
    emit_event(
        db,
        event_type="return.case_requested",
        aggregate_type="return_case",
        aggregate_id=str(case.id),
        correlation_id=correlation_id,
        producer="return",
        payload={"return_ref": case.return_ref, "state": "requested"},
    )
    advance_entity(db, case, "ReturnCase", "eligibility_review", correlation_id=correlation_id)

    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Review return eligibility for {case.return_ref}",
        required_roles=["customer_service"],
        payload={
            "case_id": str(case.id),
            "return_ref": case.return_ref,
            "proposed_by_user_id": str(actor_user_id) if actor_user_id else None,
            "four_eyes_area": "refund",
            "refund_amount": str(case.refund_amount) if case.refund_amount is not None else None,
            "next_step": "approve_eligibility",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"caseId": str(case.id), "workItemId": str(item.id)})


def _order_for_run(db, run: WorkflowRun) -> SalesOrder | None:
    """Resolve the sales order owned by an order-to-cash run.

    v2 webhook runs carry ``entity_id`` (our SalesOrder uuid) plus stable
    refs; legacy v1 runs carried the Shopify order id under ``id``.  Falls
    back to the most recent order only for in-flight legacy compatibility.
    """
    payload = run.input_json or {}
    entity_id = payload.get("entity_id")
    if entity_id:
        order = db.get(SalesOrder, _uuid(entity_id, field="entity_id"))
        if order is not None:
            return order
    for key in ("shopify_order_id", "id"):
        value = payload.get(key)
        if value is not None:
            order = db.execute(
                select(SalesOrder).where(SalesOrder.shopify_order_id == str(value))
            ).scalar_one_or_none()
            if order is not None:
                return order
    return (
        db.execute(select(SalesOrder).order_by(SalesOrder.created_at).limit(1))
        .scalars()
        .first()
    )


def _case_for_run(db, run: WorkflowRun) -> ReturnCase | None:
    """Resolve the return case owned by a return-to-refund run."""
    payload = run.input_json or {}
    entity_id = payload.get("entity_id") or payload.get("case_id")
    if entity_id:
        case = db.get(ReturnCase, _uuid(entity_id, field="entity_id"))
        if case is not None:
            return case
    order_id = payload.get("shopify_order_id")
    if order_id:
        case = db.execute(
            select(ReturnCase).where(ReturnCase.shopify_order_id == str(order_id))
        ).scalars().first()
        if case is not None:
            return case
    return None


def order_to_cash_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Webhook-driven O2C entry: intake the mirrored SalesOrder into Odoo.

    The SalesOrder was created at webhook ingest (status ``received``).
    Automated intake advances it to ``odo_drafted`` (no human gate) and
    records the Odoo sale-order create + confirm effects; the order only
    reaches ``confirmed`` after the remote effects succeed
    (``finalize_after_effect``).  The first human gate is the inventory
    reservation approval (mirrors ``order_to_cash_workflow``'s gate).
    """
    order = _order_for_run(db, run)
    if order is None:
        raise NotFoundError("sales order not found for order-to-cash run")
    for state in ("validated", "accepted", "odo_drafted"):
        advance_entity(
            db,
            order,
            "SalesOrder",
            state,
            correlation_id=correlation_id,
            context={"auto": True},
        )
    for operation in ("sale_order_create", "sale_order_confirm"):
        _record_order_effect(db, run, "odoo", operation)
    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve inventory reservation for {order.order_ref}",
        required_roles=["inventory_supervisor"],
        payload={
            "order_ref": order.order_ref,
            "next_step": "reserve",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"orderId": str(order.id), "workItemId": str(item.id)})


def return_to_refund_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Webhook-driven return entry: route the mirrored ReturnCase to review.

    The ReturnCase was created at webhook ingest (status ``requested``).  The
    first human gate is the customer-service eligibility review (same gate as
    the user-initiated ``return_entry``).
    """
    case = _case_for_run(db, run)
    if case is None:
        raise NotFoundError("return case not found for return-to-refund run")
    advance_entity(
        db,
        case,
        "ReturnCase",
        "eligibility_review",
        correlation_id=correlation_id,
    )
    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Review return eligibility for {case.return_ref}",
        required_roles=["customer_service"],
        payload={
            "case_id": str(case.id),
            "return_ref": case.return_ref,
            "proposed_by_user_id": str(actor_user_id) if actor_user_id else None,
            "four_eyes_area": "refund",
            "refund_amount": str(case.refund_amount) if case.refund_amount is not None else None,
            "next_step": "approve_eligibility",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return _run_result(run, {"caseId": str(case.id), "workItemId": str(item.id)})


def reconciliation_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    run_type = str(payload.get("run_type") or "daily")
    domains = [str(d) for d in (payload.get("domains") or ["effect"])]
    from app.services.reconciliation import default_readers

    rec_run = run_reconciliation(
        db,
        run_type=run_type,
        domains=domains,
        readers=default_readers(),
    )
    _complete_run(
        db,
        run,
        extras={
            "reconciliationRunId": str(rec_run.id),
            "summary": rec_run.summary,
        },
    )
    return _run_result(run, {"reconciliationRunId": str(rec_run.id), "summary": rec_run.summary})


COMMAND_HANDLERS.update(
    {
        "catalog-revision": catalog_revision_entry,
        "listing-publication": listing_publication_entry,
        "procurement": procurement_entry,
        "return": return_entry,
        # Webhook-driven workflow types: the domain entry runs from the v2
        # definition (``_start_txn``) after the ``workflow.accepted`` relay.
        "order-to-cash": order_to_cash_entry,
        "return-to-refund": return_to_refund_entry,
        "reconciliation": reconciliation_entry,
    }
)

# ---------------------------------------------------------------------------
# Approval continuations (run by approvals.submit_decision on approve)
# ---------------------------------------------------------------------------


def _approve_catalog_revision(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    revision = db.get(CatalogRevision, _uuid(item.payload_json.get("revision_id")))
    if revision is None:
        raise NotFoundError("catalog revision not found")
    advance_entity(
        db,
        revision,
        "CatalogRevision",
        "approved",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    revision.approved_by = _uuid(user_id)
    revision.approved_at = utc_now()

    # Content approval covers the listing gate: drive the publication to
    # publishing and record the planned external effect for the worker.
    listing = ListingPublication(
        sku=revision.sku,
        channel="shopify",
        status=ListingStatus.DRAFT,
        payload={
            "revision_id": str(revision.id),
            "title": revision.title,
            "description": revision.description,
        },
    )
    db.add(listing)
    db.flush()
    for state in ("validated", "pending_approval"):
        advance_entity(
            db,
            listing,
            "ListingPublication",
            state,
            correlation_id=run.correlation_id,
            emit=False,
        )
    advance_entity(
        db,
        listing,
        "ListingPublication",
        "publishing",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    effect = record_effect(
        db,
        target_system="shopify",
        operation="product_publish",
        idempotency_key=f"catalog:{run.id}:shopify.product_publish",
        approval_ref=run.id,
        request_hash=canonical_hash(revision.proposed or {}),
    )
    run.status = WorkflowRunStatus.RUNNING
    return {
        "revisionId": str(revision.id),
        "listingId": str(listing.id),
        "effectId": str(effect.intent_id),
    }


def _approve_listing_publication(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    listing = db.get(ListingPublication, _uuid(item.payload_json.get("listing_id")))
    if listing is None:
        raise NotFoundError("listing publication not found")
    advance_entity(
        db,
        listing,
        "ListingPublication",
        "publishing",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    effect = record_effect(
        db,
        target_system="shopify",
        operation="product_publish",
        idempotency_key=f"listing:{run.id}:shopify.product_publish",
        approval_ref=run.id,
        request_hash=canonical_hash(listing.payload or {}),
    )
    run.status = WorkflowRunStatus.RUNNING
    return {"listingId": str(listing.id), "effectId": str(effect.intent_id)}


def _approve_procurement(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = db.get(ProcurementOrder, _uuid(item.payload_json.get("po_id")))
    if order is None:
        raise NotFoundError("procurement order not found")
    advance_entity(
        db,
        order,
        "ProcurementOrder",
        "po_confirmed",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    order.approved_by = _uuid(user_id)
    order.approved_at = utc_now()
    for op in ("po_create", "po_confirm"):
        record_effect(
            db,
            target_system="odoo",
            operation=op,
            idempotency_key=f"procurement:{run.id}:odoo.{op}",
            approval_ref=run.id,
            request_hash=canonical_hash({"po_id": str(order.id), "op": op}),
        )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="confirmation",
        title=f"Confirm goods received for PO {order.id}",
        required_roles=["warehouse_staff"],
        payload={"po_id": str(order.id), "next_step": "confirm_receipt"},
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"poId": str(order.id), "workItemId": str(item_next.id)}


def _confirm_procurement_received(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = db.get(ProcurementOrder, _uuid(item.payload_json.get("po_id")))
    if order is None:
        raise NotFoundError("procurement order not found")
    advance_entity(
        db,
        order,
        "ProcurementOrder",
        "received",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    record_effect(
        db,
        target_system="odoo",
        operation="receive_transfer",
        idempotency_key=f"procurement:{run.id}:odoo.receive_transfer",
        approval_ref=run.id,
        request_hash=canonical_hash({"po_id": str(order.id), "op": "receive_transfer"}),
    )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Post bill for PO {order.id}",
        required_roles=["accountant"],
        payload={
            "po_id": str(order.id),
            "four_eyes_area": "accounting",
            "proposed_by_user_id": str(user_id),
            "next_step": "approve_bill",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"poId": str(order.id), "workItemId": str(item_next.id)}


def _approve_procurement_bill(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = db.get(ProcurementOrder, _uuid(item.payload_json.get("po_id")))
    if order is None:
        raise NotFoundError("procurement order not found")
    # Bill posting is effect-gated (P7 整改第 2 点): the remote
    # ``odoo.bill_create`` effect must succeed before ``bill_posted`` /
    # ``in_payment`` are advanced -- that advancement happens in
    # ``finalize_after_effect``, never here.
    record_effect(
        db,
        target_system="odoo",
        operation="bill_create",
        idempotency_key=f"procurement:{run.id}:odoo.bill_create",
        approval_ref=run.id,
        request_hash=canonical_hash({"po_id": str(order.id), "op": "bill_create"}),
    )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Reconcile and close PO {order.id}",
        required_roles=["accountant"],
        payload={
            "po_id": str(order.id),
            "four_eyes_area": "accounting",
            "proposed_by_user_id": str(user_id),
            "next_step": "close_po",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"poId": str(order.id), "workItemId": str(item_next.id)}


def _approve_procurement_close(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = db.get(ProcurementOrder, _uuid(item.payload_json.get("po_id")))
    if order is None:
        raise NotFoundError("procurement order not found")
    for state in ("reconciled", "closed"):
        advance_entity(
            db,
            order,
            "ProcurementOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
        )
    _complete_run(db, run, extras={"poId": str(order.id)})
    return {"poId": str(order.id), "status": "completed"}


def _proposer_for_workflow(db, run: WorkflowRun) -> str | None:
    """First work item's proposer (the customer_service who opened the case)."""
    item = (
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == run.id)
            .order_by(WorkItem.created_at)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if item is not None:
        return (item.payload_json or {}).get("proposed_by_user_id")
    return None


def _approve_return_eligibility(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    case = db.get(ReturnCase, _uuid(item.payload_json.get("case_id")))
    if case is None:
        raise NotFoundError("return case not found")
    advance_entity(
        db,
        case,
        "ReturnCase",
        "authorized",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="confirmation",
        title=f"Confirm goods received for {case.return_ref}",
        required_roles=["warehouse_staff"],
        payload={"case_id": str(case.id), "next_step": "confirm_receipt"},
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"caseId": str(case.id), "workItemId": str(item_next.id)}


def _confirm_return_received(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    case = db.get(ReturnCase, _uuid(item.payload_json.get("case_id")))
    if case is None:
        raise NotFoundError("return case not found")
    advance_entity(
        db,
        case,
        "ReturnCase",
        "received",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Inspect goods and approve disposition for {case.return_ref}",
        required_roles=["warehouse_staff"],
        payload={"case_id": str(case.id), "next_step": "approve_disposition"},
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"caseId": str(case.id), "workItemId": str(item_next.id)}


def _approve_return_disposition(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    case = db.get(ReturnCase, _uuid(item.payload_json.get("case_id")))
    if case is None:
        raise NotFoundError("return case not found")
    for state in ("inspected", "disposition_approved"):
        advance_entity(
            db,
            case,
            "ReturnCase",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
        )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Post credit note for {case.return_ref}",
        required_roles=["accountant"],
        payload={
            "case_id": str(case.id),
            "four_eyes_area": "accounting",
            "proposed_by_user_id": str(user_id),
            "invoice_posted": True,
            "next_step": "approve_credit_note",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"caseId": str(case.id), "workItemId": str(item_next.id)}


def _approve_return_credit_note(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    case = db.get(ReturnCase, _uuid(item.payload_json.get("case_id")))
    if case is None:
        raise NotFoundError("return case not found")
    # Credit-note posting is effect-gated (P7 整改第 2 点): both
    # ``odoo.credit_note_create`` and ``odoo.credit_note_validate`` must
    # succeed before ``credit_note_posted`` is advanced (finalize_after_effect
    # with the invoice-posted invariant), and ``credit_note_id`` is sourced
    # from the effect's remote_reference -- never a synthetic CN-* number.
    for op in ("credit_note_create", "credit_note_validate"):
        record_effect(
            db,
            target_system="odoo",
            operation=op,
            idempotency_key=f"return:{run.id}:odoo.{op}",
            approval_ref=run.id,
            request_hash=canonical_hash({"case_id": str(case.id), "op": op}),
        )
    proposer = _proposer_for_workflow(db, run)
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve refund amount for {case.return_ref}",
        required_roles=["finance_approver"],
        payload={
            "case_id": str(case.id),
            "return_ref": case.return_ref,
            "refund_amount": str(case.refund_amount) if case.refund_amount is not None else None,
            "four_eyes_area": "refund",
            "proposed_by_user_id": proposer,
            "next_step": "approve_refund",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"caseId": str(case.id), "workItemId": str(item_next.id)}


def _approve_return_refund(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    case = db.get(ReturnCase, _uuid(item.payload_json.get("case_id")))
    if case is None:
        raise NotFoundError("return case not found")
    advance_entity(
        db,
        case,
        "ReturnCase",
        "refund_pending",
        correlation_id=run.correlation_id,
        context={"auto": False},
        actor_user_id=user_id,
    )
    effect = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        idempotency_key=f"return:{run.id}:shopify.refund_create",
        approval_ref=run.id,
        request_hash=canonical_hash({"case_id": str(case.id), "amount": str(case.refund_amount)}),
    )
    run.status = WorkflowRunStatus.RUNNING
    return {"caseId": str(case.id), "effectId": str(effect.intent_id)}


# ---------------------------------------------------------------------------
# Order-to-cash continuations (mirror of app.workflows.vertical_slice's
# order_to_cash_workflow gates, driven through the approval service so the
# run can be completed without a live DBOS worker).
# ---------------------------------------------------------------------------


def _record_order_effect(
    db,
    run: WorkflowRun,
    target_system: str,
    operation: str,
) -> str:
    """Record a planned external effect for an order-to-cash run (idempotent).

    The intent id is a deterministic UUID5 over ``(run, operation)`` so a
    re-run of the flow replays the same ledger row instead of duplicating it.
    Odoo effects are recorded ``planned`` only -- execution belongs to the
    Odoo integration agent / worker (ADR-0004, effect ledger contract).
    """
    intent_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"order-to-cash:{run.id}:{target_system}.{operation}"
    )
    entry = record_effect(
        db,
        intent_id=intent_id,
        target_system=target_system,
        operation=operation,
        idempotency_key=f"order-to-cash:{run.id}:{target_system}.{operation}",
        approval_ref=run.id,
        request_hash=canonical_hash({"order_run": str(run.id), "operation": operation}),
    )
    return str(entry.intent_id)


def _approve_order_reserve(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = _order_for_run(db, run)
    if order is None:
        raise NotFoundError("sales order not found for workflow run")
    advance_entity(
        db,
        order,
        "SalesOrder",
        "reserved",
        correlation_id=run.correlation_id,
        context={"auto": False, "reservation_source": "stock_move"},
        actor_user_id=user_id,
    )
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve picking and shipping for {order.order_ref}",
        required_roles=["warehouse_staff"],
        payload={
            "order_ref": order.order_ref,
            "proposed_by_user_id": str(user_id),
            "next_step": "ship",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"orderRef": order.order_ref, "workItemId": str(item_next.id)}


def _approve_order_ship(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = _order_for_run(db, run)
    if order is None:
        raise NotFoundError("sales order not found for workflow run")
    for state in ("picking", "shipped"):
        advance_entity(
            db,
            order,
            "SalesOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
        )
    for operation in ("picking_create", "picking_validate"):
        _record_order_effect(db, run, "odoo", operation)
    _record_order_effect(db, run, "shopify", "fulfillment_create")
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Approve invoice posting for {order.order_ref}",
        required_roles=["accountant"],
        payload={
            "order_ref": order.order_ref,
            "four_eyes_area": "accounting",
            "proposed_by_user_id": str(user_id),
            "next_step": "invoice",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"orderRef": order.order_ref, "workItemId": str(item_next.id)}


def _approve_order_invoice(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = _order_for_run(db, run)
    if order is None:
        raise NotFoundError("sales order not found for workflow run")
    for state in ("invoiced", "in_payment"):
        advance_entity(
            db,
            order,
            "SalesOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
        )
    for operation in ("invoice_create", "invoice_validate"):
        _record_order_effect(db, run, "odoo", operation)
    item_next = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title=f"Reconcile and close order {order.order_ref}",
        required_roles=["accountant"],
        payload={
            "order_ref": order.order_ref,
            "four_eyes_area": "accounting",
            "proposed_by_user_id": str(user_id),
            "next_step": "close",
        },
        expected_version=run.version,
    )
    run.status = WorkflowRunStatus.AWAITING_APPROVAL
    return {"orderRef": order.order_ref, "workItemId": str(item_next.id)}


def _approve_order_close(db, run: WorkflowRun, item, user_id) -> dict[str, Any]:
    order = _order_for_run(db, run)
    if order is None:
        raise NotFoundError("sales order not found for workflow run")
    for state in ("reconciled", "closed"):
        advance_entity(
            db,
            order,
            "SalesOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
        )
    _complete_run(db, run, extras={"orderRef": order.order_ref})
    return {"orderRef": order.order_ref, "status": "completed"}


register_next_step("catalog-revision", "approve", _approve_catalog_revision)
register_next_step("listing-publication", "approve", _approve_listing_publication)
register_next_step("procurement", "approve_po", _approve_procurement)
register_next_step("procurement", "confirm_receipt", _confirm_procurement_received)
register_next_step("procurement", "approve_bill", _approve_procurement_bill)
register_next_step("procurement", "close_po", _approve_procurement_close)
register_next_step("return", "approve_eligibility", _approve_return_eligibility)
register_next_step("return", "confirm_receipt", _confirm_return_received)
register_next_step("return", "approve_disposition", _approve_return_disposition)
register_next_step("return", "approve_credit_note", _approve_return_credit_note)
register_next_step("return", "approve_refund", _approve_return_refund)
register_next_step("order-to-cash", "reserve", _approve_order_reserve)
register_next_step("order-to-cash", "ship", _approve_order_ship)
register_next_step("order-to-cash", "invoice", _approve_order_invoice)
register_next_step("order-to-cash", "close", _approve_order_close)


def dispatch_command(
    db,
    *,
    scope: str,
    key: str,
    command_type: str,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch a write command with idempotency and run the inline state machine.

    The caller owns the transaction: nothing here commits.  Idempotency
    records, business writes and the workflow run are all written in the same
    transaction so a commit either persists everything or nothing.
    """
    if not scope or not key:
        raise ValidationError("scope and key are required")
    request_hash = canonical_hash(payload)

    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                f"idempotency key {key!r} was already used with a different request body"
            )
        return {**(existing.result_json or {}), "replayed": True}

    handler = COMMAND_HANDLERS.get(command_type)
    if handler is None:
        raise ValidationError(f"unknown command type: {command_type}")

    correlation_id = correlation_id or str(uuid7())
    run = WorkflowRun(
        workflow_type=command_type,
        workflow_version=1,
        status=WorkflowRunStatus.RUNNING,
        correlation_id=correlation_id,
        input_json=payload,
    )
    db.add(run)
    db.flush()

    extras = handler(db, run, payload, actor_user_id, correlation_id)
    result = _run_result(run, extras)
    db.add(
        IdempotencyRecord(
            scope=scope,
            key=key,
            request_hash=request_hash,
            status="completed",
            result_json=result,
        )
    )
    db.flush()
    return {**result, "replayed": False}


@dataclass(frozen=True)
class AcceptedCommand:
    """Result of accepting a write command as a DBOS v2 workflow run."""

    workflow_id: uuid.UUID
    workflow_type: str
    status: str = "accepted"
    status_url: str = ""
    workflow_version: int = DBOS_WORKFLOW_VERSION
    orchestration_engine: str = DBOS_ORCHESTRATION_ENGINE
    replayed: bool = False
    correlation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Wire-shaped acceptance payload (statusUrl/status contract)."""
        return {
            "workflowId": str(self.workflow_id),
            "type": self.workflow_type,
            "status": self.status,
            "statusUrl": self.status_url,
            "workflowVersion": self.workflow_version,
            "orchestrationEngine": self.orchestration_engine,
            "replayed": self.replayed,
            "correlationId": self.correlation_id,
        }


def accept_command(
    db,
    *,
    command: Mapping[str, Any],
    actor_user_id: Any = None,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> AcceptedCommand:
    """Accept a write command as a DBOS v2 workflow run (P7 二.1).

    Only creates the :class:`WorkflowRun` (``orchestration_engine='dbos'``,
    ``workflow_version=2``), a minimal input and the ``workflow.accepted``
    inbox event for the worker.  No domain state migration and no external
    effects run here: the worker starts the v2 definition which performs the
    domain entry (plan: accept only, never advance).

    Idempotency: ``(scope='command', key=idempotency_key)`` replays the stored
    result for the same body and raises :class:`IdempotencyConflictError` for
    a different body.
    """
    if not idempotency_key:
        raise ValidationError("idempotency_key is required to accept a command")
    if not isinstance(command, Mapping):
        raise ValidationError("command must be a mapping with 'type' and 'payload'")
    command_type = str(command.get("type") or "")
    payload = dict(command.get("payload") or {})
    if command_type not in COMMAND_HANDLERS:
        raise ValidationError(f"unknown command type: {command_type}")

    request_hash = canonical_hash(payload)
    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == IDEMPOTENCY_SCOPE_COMMAND,
            IdempotencyRecord.key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                f"idempotency key {idempotency_key!r} was already used "
                "with a different request body"
            )
        stored = existing.result_json or {}
        return AcceptedCommand(
            workflow_id=uuid.UUID(stored["workflowId"]),
            workflow_type=command_type,
            status="accepted",
            status_url=stored.get("statusUrl", f"/v1/workflows/{stored['workflowId']}"),
            workflow_version=int(stored.get("workflowVersion", DBOS_WORKFLOW_VERSION)),
            orchestration_engine=str(
                stored.get("orchestrationEngine", DBOS_ORCHESTRATION_ENGINE)
            ),
            replayed=True,
            correlation_id=stored.get("correlationId"),
        )

    correlation_id = correlation_id or str(uuid7())
    run = WorkflowRun(
        workflow_type=command_type,
        workflow_version=DBOS_WORKFLOW_VERSION,
        orchestration_engine=DBOS_ORCHESTRATION_ENGINE,
        status=WorkflowRunStatus.ACCEPTED,
        initiated_by_user_id=_uuid(actor_user_id, field="actor_user_id"),
        correlation_id=correlation_id,
        # Minimal domain input: the v2 definition runs the domain entry from
        # here; no raw webhook / full body is copied into the event payload.
        input_json=payload,
    )
    db.add(run)
    db.flush()

    emit_event(
        db,
        event_type="workflow.accepted",
        aggregate_type="workflow",
        aggregate_id=str(run.id),
        correlation_id=correlation_id,
        producer="workflow",
        payload={
            "workflow_id": str(run.id),
            "workflow_type": command_type,
            "workflow_version": DBOS_WORKFLOW_VERSION,
            "correlation_id": correlation_id,
        },
        consumers=[ACCEPTED_EVENT_CONSUMER],
    )

    accepted = AcceptedCommand(
        workflow_id=run.id,
        workflow_type=command_type,
        status="accepted",
        status_url=f"/v1/workflows/{run.id}",
        workflow_version=DBOS_WORKFLOW_VERSION,
        orchestration_engine=DBOS_ORCHESTRATION_ENGINE,
        replayed=False,
        correlation_id=correlation_id,
    )
    db.add(
        IdempotencyRecord(
            scope=IDEMPOTENCY_SCOPE_COMMAND,
            key=idempotency_key,
            request_hash=request_hash,
            status="completed",
            result_json=accepted.as_dict(),
        )
    )
    db.flush()
    return accepted


__all__ = [
    "ACCEPTED_EVENT_CONSUMER",
    "COMMAND_HANDLERS",
    "DBOS_ORCHESTRATION_ENGINE",
    "DBOS_WORKFLOW_VERSION",
    "AcceptedCommand",
    "accept_command",
    "advance_entity",
    "canonical_hash",
    "dispatch_command",
]
