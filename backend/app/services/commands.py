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
from app.models.order import SalesOrderStatus
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


def reconciliation_entry(
    db,
    run: WorkflowRun,
    payload: dict[str, Any],
    actor_user_id: Any = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    run_type = str(payload.get("run_type") or "daily")
    domains = [str(d) for d in (payload.get("domains") or ["effect"])]
    rec_run = run_reconciliation(db, run_type=run_type, domains=domains, connectors=None)
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
    for state in ("bill_posted", "in_payment"):
        advance_entity(
            db,
            order,
            "ProcurementOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": False},
            actor_user_id=user_id,
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
    advance_entity(
        db,
        case,
        "ReturnCase",
        "credit_note_posted",
        correlation_id=run.correlation_id,
        context={
            "auto": False,
            "invoice_posted": (item.payload_json or {}).get("invoice_posted") is True,
        },
        actor_user_id=user_id,
    )
    case.credit_note_id = str(item.payload_json.get("credit_note_id") or f"CN-{uuid7()}")
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
            status="done",
            result_json=result,
        )
    )
    db.flush()
    return {**result, "replayed": False}


__all__ = [
    "COMMAND_HANDLERS",
    "advance_entity",
    "canonical_hash",
    "dispatch_command",
]
