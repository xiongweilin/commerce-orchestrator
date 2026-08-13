"""Shared, DBOS-free effect execution helpers (P7 二.4 seam).

The DBOS v2 workflow definitions drive effects through WP5's typed seam
(``app.services.effect_ledger.execute_effect``):

1. build an :class:`EffectExecutionRequest` from the ledger row + run domain
   state (:func:`build_effect_execution_request` — fail-closed: an effect
   whose required identity fields are unknown raises instead of pretending
   success);
2. execute it (:func:`execute_effect_seam`) and normalize the typed outcome;
3. apply the outcome to the ledger (``mark_dispatched`` + ``apply_outcome``)
   and advance the owning domain entities (:func:`apply_effect_outcome`).

The per-operation parameter construction is a WP4/WP5 joint contract: the
exact Odoo value fields and Shopify GID conversions belong to the WP5
adapter mapping; this module fills in what the domain rows carry today and
records the remainder as the pending WP5 integration item.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.core.logging import get_logger
from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.listing import ExternalIdMapping, ListingPublication, ListingStatus
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import WorkflowRun, WorkItem
from app.schemas.effects import (
    EFFECT_PARAMETER_MODELS,
    EffectExecutionOutcome,
    EffectExecutionRequest,
)
from app.services.commands import advance_entity
from app.services.effect_ledger import (
    apply_outcome,
    effect_transition_context,
    execute_effect,
)

logger = get_logger("commerce.effects")


def normalize_outcome(outcome: EffectExecutionOutcome) -> dict[str, Any]:
    """Normalize a WP5 typed outcome to the internal dict shape.

    Shape: ``{"status": succeeded|failed|outcome_unknown, "remote_reference",
    "response_hash", "retryable", "replayed", "error", "error_code"}``.
    """
    kind = getattr(outcome, "outcome", None)
    if kind == "succeeded":
        return {
            "status": "succeeded",
            "remote_reference": outcome.remote_reference,
            "response_hash": outcome.response_hash,
            "retryable": False,
            "replayed": bool(getattr(outcome, "replayed", False)),
            "error": None,
            "error_code": None,
        }
    if kind == "failed":
        return {
            "status": "failed",
            "remote_reference": None,
            "response_hash": outcome.response_hash,
            "retryable": bool(getattr(outcome, "retryable", False)),
            "replayed": False,
            "error": outcome.detail,
            "error_code": getattr(outcome, "error_code", None),
        }
    return {
        "status": "outcome_unknown",
        "remote_reference": None,
        "response_hash": None,
        "retryable": False,
        "replayed": False,
        "error": getattr(outcome, "detail", "outcome unknown"),
        "error_code": getattr(outcome, "error_code", "outcome_unknown"),
    }


def _run_entity_refs(db, run: WorkflowRun) -> dict[str, str]:
    """Resolve domain entity ids referenced by the run's work-item payloads."""
    refs: dict[str, str] = {}
    items = (
        db.execute(
            select(WorkItem).where(WorkItem.workflow_id == run.id).order_by(WorkItem.created_at)
        )
        .scalars()
        .all()
    )
    for item in items:
        payload = item.payload_json or {}
        for key in ("revision_id", "listing_id", "case_id", "po_id", "order_ref"):
            value = payload.get(key)
            if value and key not in refs:
                refs[key] = str(value)
    return refs


def _resolve_listing(db, run: WorkflowRun, refs: dict[str, str]) -> ListingPublication | None:
    target = refs.get("revision_id") or refs.get("listing_id")
    if not target:
        return None
    rows = (
        db.execute(
            select(ListingPublication)
            .where(
                ListingPublication.status == ListingStatus.PUBLISHING,
                ListingPublication.created_at >= run.created_at,
            )
            .order_by(ListingPublication.created_at.desc())
        )
        .scalars()
        .all()
    )
    for row in rows:
        payload_revision = (row.payload or {}).get("revision_id")
        if (payload_revision and str(payload_revision) == target) or str(row.id) == target:
            return row
    return None


def _shopify_order_gid(value: str | None) -> str | None:
    """Return the Shopify Order GID for a stored order id (numeric or GID)."""
    if not value:
        return None
    text = str(value)
    return text if text.startswith("gid://") else f"gid://shopify/Order/{text}"


def _resolve_sales_order(db, run: WorkflowRun) -> SalesOrder | None:
    """Resolve the sales order owned by an order-to-cash run.

    v2 webhook runs carry ``entity_id`` (our SalesOrder uuid) plus stable
    refs; legacy v1 runs carried the Shopify order id under ``id``.
    """
    payload = run.input_json or {}
    entity_id = payload.get("entity_id")
    if entity_id:
        try:
            order = db.get(SalesOrder, uuid.UUID(str(entity_id)))
        except ValueError:
            order = None
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
    return db.execute(select(SalesOrder).order_by(SalesOrder.created_at).limit(1)).scalars().first()


def _prior_effect_remote_reference(db, run: WorkflowRun, operation: str) -> str | None:
    """Resolve the remote id produced by a prior create effect.

    The validate effects depend on the create effect's ``remote_reference``
    (ledger chain, P7 整改第 3 点): ``approval_ref`` scopes the lookup to this
    run and ``operation`` selects the producing effect (e.g. ``picking_create``
    -> ``stock.picking.id``, ``invoice_create`` -> ``account.move.id``).
    """
    entry = (
        db.execute(
            select(EffectLedgerEntry)
            .where(
                EffectLedgerEntry.approval_ref == run.id,
                EffectLedgerEntry.operation == operation,
                EffectLedgerEntry.status.in_((EffectStatus.SUCCEEDED, EffectStatus.RECONCILED)),
            )
            .order_by(EffectLedgerEntry.created_at.desc())
        )
        .scalars()
        .first()
    )
    if entry is None:
        return None
    return entry.remote_reference


def _product_gid_for(db, listing: ListingPublication | None) -> str | None:
    """Resolve the Shopify product GID for a publish effect (fail-closed)."""
    if listing is None:
        return None
    if listing.shopify_product_gid:
        return listing.shopify_product_gid
    if listing.remote_reference:
        return listing.remote_reference
    mapping = db.execute(
        select(ExternalIdMapping).where(
            ExternalIdMapping.sku == listing.sku,
            ExternalIdMapping.channel == listing.channel,
        )
    ).scalar_one_or_none()
    if mapping is not None:
        return mapping.external_id
    return None


def build_effect_execution_request(
    db,
    run: WorkflowRun,
    effect: dict[str, Any],
) -> EffectExecutionRequest:
    """Build the WP5 typed request for a planned ledger row.

    ``effect`` carries ``effect_id`` / ``operation`` (``<system>.<op>``) /
    ``idempotency_key`` / ``request_hash`` as returned by the snapshot.
    Raises (fail-closed) when the domain row or its required identity fields
    are missing — an effect must never be reported ``succeeded`` by default.
    """
    operation = str(effect["operation"])
    effect_id = uuid.UUID(effect["effect_id"])
    refs = _run_entity_refs(db, run)
    param_model = EFFECT_PARAMETER_MODELS[operation]

    if operation == "shopify.product_publish":
        listing = _resolve_listing(db, run, refs)
        gid = _product_gid_for(db, listing)
        if not gid:
            raise ValueError(
                f"cannot build {operation} parameters: shopify product gid unknown "
                f"for run {run.id} (WP5 adapter mapping pending)"
            )
        parameters = param_model(operation=operation, gid=gid)
    elif operation == "shopify.refund_create":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        if case is None or not case.shopify_order_id:
            raise ValueError(f"cannot build {operation} parameters: return case missing")
        parameters = param_model(
            operation=operation,
            order_gid=_shopify_order_gid(case.shopify_order_id),
            amount=float(case.refund_amount) if case.refund_amount is not None else 0.0,
            note=f"refund for {case.return_ref}",
            # Fail-closed rail (plan 二.4): refunds never move real money
            # unless the deployment explicitly opts in via
            # COMMERCE_ALLOW_DEV_REFUND=true (dev store / sandbox only).
            allow_real_money=get_settings().allow_dev_refund,
        )
    elif operation == "shopify.fulfillment_create":
        order = _resolve_sales_order(db, run)
        if order is None or not order.shopify_order_id:
            raise ValueError(f"cannot build {operation} parameters: sales order missing")
        parameters = param_model(
            operation=operation, order_gid=_shopify_order_gid(order.shopify_order_id)
        )
    elif operation in {"odoo.po_create", "odoo.bill_create"}:
        order = db.get(ProcurementOrder, uuid.UUID(refs["po_id"])) if refs.get("po_id") else None
        if order is None:
            raise ValueError(f"cannot build {operation} parameters: purchase order missing")
        parameters = param_model(
            operation=operation,
            values={
                "sku": order.sku,
                "qty": str(order.qty),
                "uom": order.uom,
                "supplier": order.supplier,
                "unit_cost": str(order.unit_cost),
                "currency": order.currency,
            },
        )
    elif operation in {"odoo.po_confirm", "odoo.receive_transfer"}:
        order = db.get(ProcurementOrder, uuid.UUID(refs["po_id"])) if refs.get("po_id") else None
        if order is None or not order.odoo_po_id:
            raise ValueError(
                f"cannot build {operation} parameters: odoo PO id unknown "
                f"(create PO effect must succeed first)"
            )
        parameters = param_model(operation=operation, odoo_id=int(order.odoo_po_id))
    elif operation == "odoo.sale_order_create":
        order = _resolve_sales_order(db, run)
        if order is None:
            raise ValueError(f"cannot build {operation} parameters: sales order missing")
        parameters = param_model(
            operation=operation,
            values={
                "items": order.items or [],
                "currency": order.currency,
                "total": str(order.total),
                "customer_ref": order.customer_ref or "",
                "partner_name": "Shopify Customer",
            },
        )
    elif operation == "odoo.sale_order_confirm":
        order = _resolve_sales_order(db, run)
        odoo_id = None
        if order is not None:
            odoo_id = order.odoo_sale_order_id or _prior_effect_remote_reference(
                db, run, "sale_order_create"
            )
        if not odoo_id:
            raise ValueError(
                f"cannot build {operation} parameters: odoo sale order id unknown "
                f"(odoo.sale_order_create effect must succeed first)"
            )
        parameters = param_model(operation=operation, odoo_id=int(odoo_id))
    elif operation == "odoo.picking_validate":
        order = _resolve_sales_order(db, run)
        odoo_id = None
        if order is not None:
            # The connector requires the stock.picking id, not the sale order
            # id: prefer the domain column written back from picking_create,
            # fall back to the create effect's ledger remote_reference.
            odoo_id = order.odoo_picking_id or _prior_effect_remote_reference(
                db, run, "picking_create"
            )
        if not odoo_id:
            raise ValueError(
                f"cannot build {operation} parameters: stock.picking id unknown "
                f"(odoo.picking_create effect must succeed first)"
            )
        parameters = param_model(operation=operation, odoo_id=int(odoo_id))
    elif operation == "odoo.invoice_validate":
        order = _resolve_sales_order(db, run)
        odoo_id = None
        if order is not None:
            # account.move id, not the sale order id.
            odoo_id = order.odoo_invoice_id or _prior_effect_remote_reference(
                db, run, "invoice_create"
            )
        if not odoo_id:
            raise ValueError(
                f"cannot build {operation} parameters: account.move id unknown "
                f"(odoo.invoice_create effect must succeed first)"
            )
        parameters = param_model(operation=operation, odoo_id=int(odoo_id))
    elif operation in {"odoo.picking_create", "odoo.invoice_create"}:
        order = _resolve_sales_order(db, run)
        if order is None:
            raise ValueError(f"cannot build {operation} parameters: sales order missing")
        if operation == "odoo.picking_create":
            if not order.odoo_sale_order_id:
                raise ValueError(
                    f"cannot build {operation} parameters: odoo sale order id unknown "
                    f"(odoo.sale_order_create effect must succeed first)"
                )
            values = {"sale_order_id": int(order.odoo_sale_order_id)}
        else:
            if not order.odoo_sale_order_id:
                raise ValueError(
                    f"cannot build {operation} parameters: odoo sale order id unknown "
                    f"(odoo.sale_order_create effect must succeed first)"
                )
            values = {
                "sale_order_id": int(order.odoo_sale_order_id),
                "order_ref": order.order_ref,
            }
        parameters = param_model(operation=operation, values=values)
    elif operation == "odoo.credit_note_create":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        if case is None:
            raise ValueError(f"cannot build {operation} parameters: return case missing")
        parameters = param_model(
            operation=operation,
            values={
                "invoice_origin": case.order_ref or case.shopify_order_id or case.return_ref,
                "currency": case.currency or "CNY",
                "amount": str(case.refund_amount) if case.refund_amount is not None else "0",
            },
        )
    elif operation == "odoo.credit_note_validate":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        odoo_id = None
        if case is not None:
            odoo_id = case.odoo_credit_note_id or _prior_effect_remote_reference(
                db, run, "credit_note_create"
            )
        if not odoo_id:
            raise ValueError(
                f"cannot build {operation} parameters: account.move id unknown "
                f"(odoo.credit_note_create effect must succeed first)"
            )
        parameters = param_model(operation=operation, odoo_id=int(odoo_id))
    elif operation == "odoo.product_create":
        revision = (
            db.get(CatalogRevision, uuid.UUID(refs["revision_id"]))
            if refs.get("revision_id")
            else None
        )
        if revision is None:
            raise ValueError(f"cannot build {operation} parameters: catalog revision missing")
        parameters = param_model(
            operation=operation,
            values={
                "name": revision.title or revision.sku,
                "default_code": revision.sku,
                "type": "product",
                "sale_ok": True,
                "purchase_ok": True,
            },
        )
    elif operation == "odoo.product_update":
        revision = (
            db.get(CatalogRevision, uuid.UUID(refs["revision_id"]))
            if refs.get("revision_id")
            else None
        )
        odoo_id = _prior_effect_remote_reference(db, run, "product_create")
        if revision is None or not odoo_id:
            raise ValueError(
                f"cannot build {operation} parameters: odoo product id unknown "
                f"(odoo.product_create effect must succeed first)"
            )
        parameters = param_model(
            operation=operation,
            odoo_id=int(odoo_id),
            values=dict(revision.proposed or {}),
        )
    elif operation == "shopify.product_update":
        listing = _resolve_listing(db, run, refs)
        gid = _product_gid_for(db, listing)
        if not gid:
            raise ValueError(f"cannot build {operation} parameters: shopify product gid unknown")
        revision = (
            db.get(CatalogRevision, uuid.UUID(refs["revision_id"]))
            if refs.get("revision_id")
            else None
        )
        parameters = param_model(
            operation=operation,
            gid=gid,
            payload=dict(revision.proposed or {}) if revision is not None else {},
        )
    elif operation == "odoo.stock_move_create":
        parameters = param_model(operation=operation, values={"source": "commerce-orchestrator"})
    else:
        raise ValueError(f"no parameter builder for effect operation {operation!r}")

    return EffectExecutionRequest(
        intent_id=effect_id,
        operation=operation,
        parameters=parameters,
        idempotency_key=effect.get("idempotency_key"),
        request_hash=effect.get("request_hash"),
        correlation_id=run.correlation_id,
        approval_ref=run.id,
    )


def execute_effect_seam(request: EffectExecutionRequest) -> EffectExecutionOutcome:
    """Run one effect through the WP5 typed seam (never string-inferred)."""
    return execute_effect(request)


def finalize_after_effect(
    db,
    run: WorkflowRun,
    operation: str,
    outcome: EffectExecutionOutcome,
) -> None:
    """Advance domain entities after a confirmed successful effect."""
    normalized = normalize_outcome(outcome)
    if normalized["status"] != "succeeded":
        return
    refs = _run_entity_refs(db, run)
    remote = normalized["remote_reference"]

    if operation == "shopify.product_publish":
        listing = _resolve_listing(db, run, refs)
        if listing is not None and listing.status == ListingStatus.PUBLISHING:
            listing.shopify_product_gid = remote or listing.shopify_product_gid
            listing.remote_reference = remote
            advance_entity(
                db,
                listing,
                "ListingPublication",
                "active",
                correlation_id=run.correlation_id,
                context={"auto": True},
            )
        revision = (
            db.get(CatalogRevision, uuid.UUID(refs["revision_id"]))
            if refs.get("revision_id")
            else None
        )
        if revision is not None:
            advance_entity(
                db,
                revision,
                "CatalogRevision",
                "official",
                correlation_id=run.correlation_id,
                context={"auto": True, "listing_published": True},
            )
            others = (
                db.execute(
                    select(CatalogRevision).where(
                        CatalogRevision.sku == revision.sku,
                        CatalogRevision.id != revision.id,
                        CatalogRevision.status.in_(
                            (CatalogRevisionStatus.OFFICIAL, CatalogRevisionStatus.APPROVED)
                        ),
                    )
                )
                .scalars()
                .all()
            )
            for other in others:
                advance_entity(
                    db,
                    other,
                    "CatalogRevision",
                    "superseded",
                    correlation_id=run.correlation_id,
                    context={"auto": True},
                )

    elif operation == "shopify.refund_create":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        if case is not None:
            case.shopify_refund_gid = remote or case.shopify_refund_gid
            advance_entity(
                db,
                case,
                "ReturnCase",
                "refund_succeeded",
                correlation_id=run.correlation_id,
                context={"auto": True},
            )

    elif operation in {"odoo.po_create", "odoo.po_confirm", "odoo.receive_transfer"}:
        order = db.get(ProcurementOrder, uuid.UUID(refs["po_id"])) if refs.get("po_id") else None
        if order is not None:
            order.odoo_po_id = remote or order.odoo_po_id

    elif operation == "odoo.bill_create":
        order = db.get(ProcurementOrder, uuid.UUID(refs["po_id"])) if refs.get("po_id") else None
        if order is not None:
            order.odoo_bill_id = remote or order.odoo_bill_id
            # P7 整改第 2 点: bill posting only advances after the remote
            # effect succeeded (never simulated at approval time).
            if order.status == ProcurementStatus.RECEIVED:
                advance_entity(
                    db,
                    order,
                    "ProcurementOrder",
                    "bill_posted",
                    correlation_id=run.correlation_id,
                    context={"auto": False},
                )
            if order.status == ProcurementStatus.BILL_POSTED:
                advance_entity(
                    db,
                    order,
                    "ProcurementOrder",
                    "in_payment",
                    correlation_id=run.correlation_id,
                    context={"auto": False},
                )

    elif operation in {"odoo.sale_order_create", "odoo.sale_order_confirm"}:
        order = _resolve_sales_order(db, run)
        if order is not None:
            order.odoo_sale_order_id = remote or order.odoo_sale_order_id
            if (
                operation == "odoo.sale_order_confirm"
                and order.status == SalesOrderStatus.ODO_DRAFTED
            ):
                # The intake completes only once the remote sale order is
                # created AND confirmed (mirrors the v1 slice's ordering).
                advance_entity(
                    db,
                    order,
                    "SalesOrder",
                    "confirmed",
                    correlation_id=run.correlation_id,
                    context={"auto": True},
                )

    elif operation == "odoo.picking_create":
        order = _resolve_sales_order(db, run)
        if order is not None:
            order.odoo_picking_id = remote or order.odoo_picking_id

    elif operation == "odoo.invoice_create":
        order = _resolve_sales_order(db, run)
        if order is not None:
            order.odoo_invoice_id = remote or order.odoo_invoice_id

    elif operation == "odoo.credit_note_create":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        if case is not None:
            if remote:
                # The business credit-note number comes from the remote
                # effect (never a synthetic CN-* value).
                case.odoo_credit_note_id = remote
                case.credit_note_id = remote
            if case.status == ReturnStatus.DISPOSITION_APPROVED:
                # Credit notes are only posted against a posted invoice
                # (state machine invariant; effect_transition_context passes
                # the same ``invoice_posted`` attestation to the ledger).
                advance_entity(
                    db,
                    case,
                    "ReturnCase",
                    "credit_note_posted",
                    correlation_id=run.correlation_id,
                    context={"auto": False, "invoice_posted": True},
                )


def apply_effect_outcome(
    db,
    *,
    workflow_id: str,
    effect_id: uuid.UUID,
    operation: str,
    outcome: EffectExecutionOutcome,
) -> None:
    """Ledger terminal marking (``apply_outcome``) + domain finalization.

    Callers perform the ``planned -> dispatched`` transition themselves (one
    mark per execution attempt) so retries never double-dispatch.  A missing
    ledger row is tolerated (legacy direct-executed steps) and only
    finalization runs.
    """
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    entry = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == effect_id)
    ).scalar_one_or_none()
    context = effect_transition_context(operation)
    if entry is not None:
        apply_outcome(db, effect_id, outcome, context=context)
    else:
        logger.warning(
            "effect_row_missing_skipping_mark",
            effect_id=str(effect_id),
            workflow_id=workflow_id,
        )
    finalize_after_effect(db, run, operation, outcome)


__all__ = [
    "apply_effect_outcome",
    "build_effect_execution_request",
    "execute_effect_seam",
    "finalize_after_effect",
    "normalize_outcome",
]
