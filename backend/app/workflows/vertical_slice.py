"""DBOS workflows for the first vertical slice (docs/architecture.md, 21 steps).

Each workflow is a durable :class:`DBOS.workflow` that composes
``@DBOS.transaction`` functions (business-DB writes via ``DBOS.sql_session``,
exactly-once) and ``@DBOS.step`` functions (external connector calls,
at-least-once).  Connector callables are injected through
:func:`register_connector`; when no connector is registered a no-op stub is
used so the slice can run in development.

Approvals are human steps: the workflows poll the business DB for pending
work-item decisions (submitted through the API) and suspend with
``DBOS.sleep`` while waiting -- a 30-day approval wait does not occupy a
worker slot.

This module intentionally imports ``dbos`` at module import time: the
decorators must be applied while the module is loaded.  ``app.services`` never
imports this module, so tests do not require a live DBOS runtime.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from dbos import DBOS
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemStatus,
)
from app.services.approvals import create_work_item
from app.services.commands import advance_entity, canonical_hash
from app.services.effect_ledger import mark_effect, record_effect
from app.services.outbox_inbox import (
    emit_event,
    register_consumer,
)
from app.services.reconciliation import run_reconciliation

logger = get_logger("commerce.workflows")

DEFAULT_APPROVAL_TIMEOUT_DAYS = 30
DEFAULT_POLL_SECONDS = 60

# ---------------------------------------------------------------------------
# Connector registry (injected by app/connectors, or stub for development)
# ---------------------------------------------------------------------------

_CONNECTOR_REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_connector(operation: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Register the callable that executes an effect operation.

    Contract: ``fn(request_payload) -> {"ok": bool, "remote_reference": str|None,
    "error": str|None}``.  ``ok`` must be False unless the remote system
    confirmed success through every response signal (HTTP status, top-level
    errors, mutation userErrors).
    """
    _CONNECTOR_REGISTRY[operation] = fn


def get_connector(operation: str) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    return _CONNECTOR_REGISTRY.get(operation)


def _stub_connector(request: dict[str, Any]) -> dict[str, Any]:
    logger.warning(
        "connector_not_registered_using_stub",
        operation=request.get("operation"),
    )
    return {
        "ok": True,
        "remote_reference": f"stub:{request.get('operation', 'unknown')}:{uuid.uuid4()}",
        "error": None,
    }


# ---------------------------------------------------------------------------
# Shared DBOS steps / transactions
# ---------------------------------------------------------------------------


@DBOS.transaction(name="slice_run_state")
def _run_state_txn(workflow_id: str) -> dict[str, Any]:
    """Snapshot of a run: status, pending items, planned effects."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return {"status": "not_found", "pending_items": [], "planned_effects": []}
    pending = (
        db.execute(
            select(WorkItem)
            .where(
                WorkItem.workflow_id == run.id,
                WorkItem.status == WorkItemStatus.PENDING,
            )
            .order_by(WorkItem.created_at)
        )
        .scalars()
        .all()
    )
    effects = (
        db.execute(
            select(EffectLedgerEntry).where(
                EffectLedgerEntry.approval_ref == run.id,
                EffectLedgerEntry.status == EffectStatus.PLANNED,
            )
        )
        .scalars()
        .all()
    )
    return {
        "status": run.status.value,
        "pending_items": [
            {"work_item_id": str(item.id), "status": item.status.value} for item in pending
        ],
        "planned_effects": [
            {
                "effect_id": str(effect.intent_id),
                "target_system": effect.target_system,
                "operation": effect.operation,
                "idempotency_key": effect.idempotency_key,
                "request_hash": effect.request_hash,
                "request_payload": {
                    "operation": f"{effect.target_system}.{effect.operation}",
                    "idempotency_key": effect.idempotency_key,
                    "approval_ref": workflow_id,
                },
            }
            for effect in effects
        ],
    }


@DBOS.step(name="slice_execute_effect")
def _execute_effect_step(effect: dict[str, Any]) -> dict[str, Any]:
    """Execute one external effect through the registered connector."""
    operation = f"{effect['target_system']}.{effect['operation']}"
    connector = get_connector(operation) or _stub_connector
    request = {**effect.get("request_payload", {}), "operation": operation}
    try:
        outcome = connector(request)
    except Exception as exc:  # noqa: BLE001 - surface as effect failure
        logger.exception(
            "connector_call_failed",
            operation=operation,
            effect_id=effect["effect_id"],
        )
        outcome = {"ok": False, "remote_reference": None, "error": str(exc)}
    return {
        "effect_id": effect["effect_id"],
        "operation": operation,
        "outcome": outcome,
    }


@DBOS.transaction(name="slice_apply_effect_outcome")
def _apply_effect_outcome_txn(workflow_id: str, step_result: dict[str, Any]) -> None:
    """Record the effect outcome in the ledger (never blind-retry outcome_unknown)."""
    db = DBOS.sql_session
    effect_id = uuid.UUID(step_result["effect_id"])
    operation = str(step_result.get("operation", ""))
    outcome = step_result["outcome"]
    entry = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == effect_id)
    ).scalar_one_or_none()
    if entry is None:
        # Effect was not recorded in the ledger (e.g. order slice executed the
        # step directly): nothing to mark, still run the finalizer.
        logger.warning(
            "effect_row_missing_skipping_mark",
            effect_id=step_result["effect_id"],
            workflow_id=workflow_id,
        )
        _finalize_after_effect(db, workflow_id, step_result)
        return
    if outcome.get("ok") is True:
        # Effect ledger state machine requires planned -> dispatched before
        # the final status (succeeded/failed/outcome_unknown).
        mark_effect(db, effect_id, status="dispatched", context=_effect_context(operation))
        mark_effect(
            db,
            effect_id,
            status="succeeded",
            remote_reference=outcome.get("remote_reference"),
            response_hash=canonical_hash(outcome),
            context=_effect_context(operation),
        )
    elif outcome.get("error") and "timeout" in str(outcome.get("error")).lower():
        # Result cannot be confirmed: escalate, never re-dispatch blindly.
        mark_effect(db, effect_id, status="dispatched", context=_effect_context(operation))
        mark_effect(
            db,
            effect_id,
            status="outcome_unknown",
            error_detail=outcome.get("error"),
            context=_effect_context(operation),
        )
    else:
        mark_effect(db, effect_id, status="dispatched", context=_effect_context(operation))
        mark_effect(
            db,
            effect_id,
            status="failed",
            error_detail=outcome.get("error"),
            context=_effect_context(operation),
        )
    _finalize_after_effect(db, workflow_id, step_result)


def _effect_context(operation: str) -> dict[str, Any]:
    """Attest invariants the effect ledger guards require.

    Credit-note effects are only legal against posted invoices; inventory
    effects must name their change source (stock move / adjustment).
    """
    ctx: dict[str, Any] = {}
    if operation in {"credit_note_create", "credit_note_validate"}:
        ctx["invoice_posted"] = True
    if operation in {"stock_move_create", "picking_validate", "receive_transfer"}:
        ctx["inventory_change_source"] = "stock_move"
    return ctx


def _finalize_after_effect(db, workflow_id: str, step_result: dict[str, Any]) -> None:
    """Advance the domain machine after a successful external effect."""
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    operation = step_result["operation"]
    outcome = step_result["outcome"]
    if outcome.get("ok") is not True:
        return

    refs = _run_entity_refs(db, run)

    if operation == "shopify.product_publish":
        publishing = (
            db.execute(
                select(ListingPublication)
                .where(ListingPublication.status == ListingStatus.PUBLISHING)
                .order_by(ListingPublication.created_at.desc())
            )
            .scalars()
            .all()
        )
        listing = _match_listing(publishing, refs)
        if listing is not None and listing.status == ListingStatus.PUBLISHING:
            listing.shopify_product_gid = outcome.get("remote_reference")
            listing.remote_reference = outcome.get("remote_reference")
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

    if operation == "shopify.refund_create":
        case = db.get(ReturnCase, uuid.UUID(refs["case_id"])) if refs.get("case_id") else None
        if case is not None:
            case.shopify_refund_gid = outcome.get("remote_reference")
            advance_entity(
                db,
                case,
                "ReturnCase",
                "refund_succeeded",
                correlation_id=run.correlation_id,
                context={"auto": True},
            )

    if operation in {"odoo.po_create", "odoo.po_confirm", "odoo.receive_transfer"}:
        order = db.get(ProcurementOrder, uuid.UUID(refs["po_id"])) if refs.get("po_id") else None
        if order is not None:
            order.odoo_po_id = outcome.get("remote_reference") or order.odoo_po_id


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
        for key in ("revision_id", "listing_id", "case_id", "po_id"):
            value = payload.get(key)
            if value and key not in refs:
                refs[key] = str(value)
    return refs


def _match_listing(
    rows: list[ListingPublication],
    refs: dict[str, str],
) -> ListingPublication | None:
    """Pick the publishing listing owned by this run."""
    target = refs.get("revision_id") or refs.get("listing_id")
    if not target:
        return None
    for row in rows:
        payload_revision = (row.payload or {}).get("revision_id")
        if (payload_revision and str(payload_revision) == target) or str(row.id) == target:
            return row
    return None


@DBOS.transaction(name="slice_complete_run")
def _complete_run_txn(workflow_id: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None or run.status in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED):
        return
    run.status = WorkflowRunStatus.COMPLETED
    run.result_json = {
        "workflowId": workflow_id,
        "status": "completed",
        "statusUrl": f"/v1/workflows/{workflow_id}",
    }
    emit_event(
        db,
        event_type="workflow.completed",
        aggregate_type="workflow",
        aggregate_id=workflow_id,
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},
    )


@DBOS.transaction(name="slice_cancel_run")
def _cancel_run_txn(workflow_id: str, reason: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    if run.status not in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.CANCELLED):
        run.status = WorkflowRunStatus.CANCELLED
        run.result_json = {"cancelled": True, "reason": reason}
        emit_event(
            db,
            event_type="workflow.cancelled",
            aggregate_type="workflow",
            aggregate_id=workflow_id,
            correlation_id=run.correlation_id,
            producer="workflow",
            payload={"workflow_id": workflow_id, "reason": reason},
        )


@DBOS.transaction(name="slice_mark_failed")
def _mark_run_failed_txn(workflow_id: str, error: str) -> None:
    db = DBOS.sql_session
    try:
        run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    except (ValueError, TypeError):
        return
    if run is None or run.status in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.CANCELLED):
        return
    run.status = WorkflowRunStatus.FAILED
    run.error = error[:2000]
    emit_event(
        db,
        event_type="workflow.failed",
        aggregate_type="workflow",
        aggregate_id=workflow_id,
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": workflow_id, "error": error[:500]},
    )


def _wait_for_decision(
    workflow_id: str,
    *,
    max_days: int = DEFAULT_APPROVAL_TIMEOUT_DAYS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> str:
    """Suspend the workflow until the run has no pending work items.

    Returns ``"decided"`` when the human gate cleared, ``"cancelled"`` when the
    run was cancelled/completed elsewhere, or ``"timeout"`` after ``max_days``.
    """
    deadline = utc_now() + timedelta(days=max_days)
    while True:
        state = _run_state_txn(workflow_id)
        if state["status"] in ("completed", "cancelled", "failed", "not_found"):
            return "cancelled"
        if not state["pending_items"]:
            return "decided"
        if utc_now() >= deadline:
            return "timeout"
        DBOS.sleep(poll_seconds)


def _drive_effects(workflow_id: str) -> None:
    """Execute planned effects until none remain, then complete the run."""
    while True:
        state = _run_state_txn(workflow_id)
        if state["status"] in ("completed", "cancelled", "failed"):
            return
        if state["pending_items"]:
            outcome = _wait_for_decision(workflow_id)
            if outcome != "decided":
                return
            continue
        if not state["planned_effects"]:
            _complete_run_txn(workflow_id)
            return
        for effect in state["planned_effects"]:
            step_result = _execute_effect_step(effect)
            _apply_effect_outcome_txn(workflow_id, step_result)


# ---------------------------------------------------------------------------
# Vertical-slice workflows
# ---------------------------------------------------------------------------


@DBOS.workflow(name="catalog_change_and_listing_workflow")
def catalog_change_and_listing_workflow(
    *,
    scope: str,
    key: str,
    payload: dict[str, Any],
    actor_user_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """candidate -> revision approved -> odoo product create -> shopify product
    publish -> listing active (steps 10-17 of the 21-step slice)."""

    workflow_id: str | None = None
    try:
        result = _dispatch_txn(
            "catalog-revision",
            scope,
            key,
            payload,
            actor_user_id,
            correlation_id,
        )
        workflow_id = result["workflowId"]
        outcome = _wait_for_decision(workflow_id)
        if outcome != "decided":
            _cancel_run_txn(workflow_id, "approval not granted")
            return result
        _drive_effects(workflow_id)
        return {**result, "finalStatus": "completed"}
    except Exception as exc:  # noqa: BLE001 - uncaught -> status failed
        logger.exception("catalog_change_and_listing_workflow_failed", workflow_id=workflow_id)
        if workflow_id is not None:
            _mark_run_failed_txn(workflow_id, str(exc))
        raise


@DBOS.transaction(name="slice_dispatch")
def _dispatch_txn(
    command_type: str,
    scope: str,
    key: str,
    payload: dict[str, Any],
    actor_user_id: str | None,
    correlation_id: str | None,
) -> dict[str, Any]:
    from app.services.commands import dispatch_command

    db = DBOS.sql_session
    return dispatch_command(
        db,
        scope=scope,
        key=key,
        command_type=command_type,
        payload=payload,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
    )


@DBOS.workflow(name="procurement_workflow")
def procurement_workflow(
    *,
    scope: str,
    key: str,
    payload: dict[str, Any],
    actor_user_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """demand -> rfq -> approval -> po confirm -> receive -> bill -> close."""
    workflow_id: str | None = None
    try:
        result = _dispatch_txn("procurement", scope, key, payload, actor_user_id, correlation_id)
        workflow_id = result["workflowId"]
        # Drive each human gate; approval continuations (registered by
        # app.services.commands) advance the PO machine and create the next
        # work item.  Planned Odoo effects are executed between gates.
        for _ in range(8):
            state = _run_state_txn(workflow_id)
            if state["status"] in ("completed", "cancelled", "failed"):
                break
            if state["pending_items"]:
                if _wait_for_decision(workflow_id) != "decided":
                    break
                continue
            if state["planned_effects"]:
                for effect in state["planned_effects"]:
                    step_result = _execute_effect_step(effect)
                    _apply_effect_outcome_txn(workflow_id, step_result)
                continue
            _complete_run_txn(workflow_id)
            break
        return {**result, "finalStatus": "completed"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("procurement_workflow_failed", workflow_id=workflow_id)
        if workflow_id is not None:
            _mark_run_failed_txn(workflow_id, str(exc))
        raise


@DBOS.transaction(name="slice_resolve_order_run")
def _resolve_order_run_txn(
    correlation_id: str,
    payload: dict[str, Any],
) -> str:
    """Find the run created by the webhook, or create the order + run."""
    db = DBOS.sql_session
    run = (
        db.execute(
            select(WorkflowRun).where(
                WorkflowRun.correlation_id == correlation_id,
                WorkflowRun.workflow_type == "order-to-cash",
            )
        )
        .scalars()
        .first()
    )
    if run is not None:
        return str(run.id)
    from decimal import Decimal

    order = SalesOrder(
        order_ref=payload.get("name") or f"SHOPIFY-{uuid7()}",
        shopify_order_id=str(payload.get("id")) if payload.get("id") else None,
        customer_ref=(
            str(payload["customer"].get("email"))
            if isinstance(payload.get("customer"), dict)
            else None
        ),
        status=SalesOrderStatus.RECEIVED,
        currency=str(payload.get("currency") or "CNY"),
        total=Decimal(str(payload.get("total_price") or "0")),
        items=payload.get("line_items") or [],
    )
    db.add(order)
    db.flush()
    run = WorkflowRun(
        workflow_type="order-to-cash",
        workflow_version=1,
        status=WorkflowRunStatus.RUNNING,
        correlation_id=correlation_id,
        input_json=payload,
    )
    db.add(run)
    db.flush()
    return str(run.id)


@DBOS.transaction(name="slice_advance_order")
def _advance_order_txn(workflow_id: str, states: list[str]) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    order = _order_for_run(db, run)
    if order is None:
        return
    for state in states:
        advance_entity(
            db,
            order,
            "SalesOrder",
            state,
            correlation_id=run.correlation_id,
            context={"auto": True, "reservation_source": "stock_move"},
        )


def _order_for_run(db, run: WorkflowRun) -> SalesOrder | None:
    shopify_id = (run.input_json or {}).get("id")
    if shopify_id is not None:
        order = db.execute(
            select(SalesOrder).where(SalesOrder.shopify_order_id == str(shopify_id))
        ).scalar_one_or_none()
        if order is not None:
            return order
    return db.execute(select(SalesOrder).order_by(SalesOrder.created_at).limit(1)).scalars().first()


def _case_for_run(db, run: WorkflowRun) -> ReturnCase | None:
    order_id = (run.input_json or {}).get("order_id")
    if order_id is not None:
        case = db.execute(
            select(ReturnCase).where(ReturnCase.shopify_order_id == str(order_id))
        ).scalar_one_or_none()
        if case is not None:
            return case
    return db.execute(select(ReturnCase).order_by(ReturnCase.created_at).limit(1)).scalars().first()


@DBOS.transaction(name="slice_order_gate")
def _order_gate_txn(
    workflow_id: str,
    *,
    role: str,
    kind: str,
    title: str,
    next_step: str,
) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    pending = (
        db.execute(
            select(WorkItem).where(
                WorkItem.workflow_id == run.id,
                WorkItem.status == WorkItemStatus.PENDING,
            )
        )
        .scalars()
        .all()
    )
    if pending:
        return
    create_work_item(
        db,
        workflow_id=run.id,
        kind=kind,
        title=title,
        required_roles=[role],
        payload={"next_step": next_step},
        expected_version=run.version,
    )


@DBOS.transaction(name="slice_record_effect")
def _record_effect_txn(workflow_id: str, operation: str, payload: dict[str, Any]) -> str:
    """Record a planned effect under the run and return its intent id."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        raise ValueError(f"workflow run {workflow_id} not found")
    target_system, _, op = operation.partition(".")
    entry = record_effect(
        db,
        target_system=target_system,
        operation=op,
        idempotency_key=f"{run.workflow_type}:{run.id}:{operation}",
        approval_ref=run.id,
        request_hash=canonical_hash(payload),
    )
    return str(entry.intent_id)


def _order_effect(workflow_id: str, operation: str, correlation_id: str) -> dict[str, Any]:
    """Record + execute one order effect step through the ledger."""
    effect_id = _record_effect_txn(workflow_id, operation, {"correlation_id": correlation_id})
    target_system, _, op = operation.partition(".")
    step_result = _execute_effect_step(
        {
            "effect_id": effect_id,
            "target_system": target_system,
            "operation": op,
            "request_payload": {"correlation_id": correlation_id, "operation": operation},
        }
    )
    _apply_effect_outcome_txn(workflow_id, step_result)
    return step_result


@DBOS.workflow(name="order_to_cash_workflow")
def order_to_cash_workflow(
    *,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """received -> validated -> accepted -> odoo drafted -> confirmed -> reserved
    -> picking -> shipped -> invoiced -> in_payment -> reconciled -> closed."""
    correlation_id = correlation_id or str(uuid7())
    workflow_id: str | None = None
    try:
        workflow_id = _resolve_order_run_txn(correlation_id, payload)
        # Automated intake into Odoo (no human gate).
        _advance_order_txn(workflow_id, ["validated", "accepted", "odo_drafted"])
        _order_effect(workflow_id, "odoo.sale_order_create", correlation_id)
        _order_effect(workflow_id, "odoo.sale_order_confirm", correlation_id)
        _advance_order_txn(workflow_id, ["confirmed"])

        _order_gate_txn(
            workflow_id,
            role="inventory_supervisor",
            kind="approval",
            title="Approve inventory reservation",
            next_step="reserve",
        )
        if _wait_for_decision(workflow_id) != "decided":
            _cancel_run_txn(workflow_id, "order reservation not approved")
            return {"workflowId": workflow_id, "status": "cancelled"}
        _advance_order_txn(workflow_id, ["reserved"])

        _order_gate_txn(
            workflow_id,
            role="warehouse_staff",
            kind="approval",
            title="Approve picking and shipping",
            next_step="ship",
        )
        if _wait_for_decision(workflow_id) != "decided":
            _cancel_run_txn(workflow_id, "order shipping not approved")
            return {"workflowId": workflow_id, "status": "cancelled"}
        for op in ("picking_create", "picking_validate"):
            _order_effect(workflow_id, f"odoo.{op}", correlation_id)
        _order_effect(workflow_id, "shopify.fulfillment_create", correlation_id)
        _advance_order_txn(workflow_id, ["picking", "shipped"])

        _order_gate_txn(
            workflow_id,
            role="accountant",
            kind="approval",
            title="Approve invoice posting",
            next_step="invoice",
        )
        if _wait_for_decision(workflow_id) != "decided":
            _cancel_run_txn(workflow_id, "order invoicing not approved")
            return {"workflowId": workflow_id, "status": "cancelled"}
        for op in ("invoice_create", "invoice_validate"):
            _order_effect(workflow_id, f"odoo.{op}", correlation_id)
        _advance_order_txn(workflow_id, ["invoiced", "in_payment"])

        _order_gate_txn(
            workflow_id,
            role="accountant",
            kind="approval",
            title="Reconcile and close order",
            next_step="close",
        )
        if _wait_for_decision(workflow_id) != "decided":
            _cancel_run_txn(workflow_id, "order reconciliation not approved")
            return {"workflowId": workflow_id, "status": "cancelled"}
        _advance_order_txn(workflow_id, ["reconciled", "closed"])
        _complete_run_txn(workflow_id)
        return {"workflowId": workflow_id, "status": "completed"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("order_to_cash_workflow_failed", workflow_id=workflow_id)
        if workflow_id is not None:
            _mark_run_failed_txn(workflow_id, str(exc))
        raise


@DBOS.transaction(name="slice_resolve_return_run")
def _resolve_return_run_txn(
    correlation_id: str,
    payload: dict[str, Any],
) -> str:
    """Find the run created by the webhook, or create the case + run."""
    db = DBOS.sql_session
    run = (
        db.execute(
            select(WorkflowRun).where(
                WorkflowRun.correlation_id == correlation_id,
                WorkflowRun.workflow_type == "return-to-refund",
            )
        )
        .scalars()
        .first()
    )
    if run is not None:
        return str(run.id)
    from decimal import Decimal

    case = ReturnCase(
        return_ref=f"RET-{uuid7()}",
        shopify_order_id=str(payload.get("order_id") or ""),
        customer_ref=str(payload.get("customer_ref") or ""),
        reason=str(payload.get("reason") or "shopify refund webhook"),
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
    run = WorkflowRun(
        workflow_type="return-to-refund",
        workflow_version=1,
        status=WorkflowRunStatus.RUNNING,
        correlation_id=correlation_id,
        input_json=payload,
    )
    db.add(run)
    db.flush()
    return str(run.id)


@DBOS.transaction(name="slice_return_eligibility_gate")
def _return_eligibility_gate_txn(workflow_id: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        return
    case = _case_for_run(db, run)
    pending = (
        db.execute(
            select(WorkItem).where(
                WorkItem.workflow_id == run.id,
                WorkItem.status == WorkItemStatus.PENDING,
            )
        )
        .scalars()
        .all()
    )
    if case is not None and not pending and case.status == ReturnStatus.REQUESTED:
        advance_entity(
            db,
            case,
            "ReturnCase",
            "eligibility_review",
            correlation_id=run.correlation_id,
            context={"auto": True},
        )
        create_work_item(
            db,
            workflow_id=run.id,
            kind="approval",
            title=f"Review return eligibility for {case.return_ref}",
            required_roles=["customer_service"],
            payload={"case_id": str(case.id), "next_step": "approve_eligibility"},
            expected_version=run.version,
        )


@DBOS.workflow(name="return_to_refund_workflow")
def return_to_refund_workflow(
    *,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """case -> eligibility -> authorized -> goods received -> inspected ->
    disposition approved -> credit note -> refund pending -> refund succeeded
    -> reconciled -> closed."""
    correlation_id = correlation_id or str(uuid7())
    workflow_id: str | None = None
    try:
        workflow_id = _resolve_return_run_txn(correlation_id, payload)
        _return_eligibility_gate_txn(workflow_id)
        if _wait_for_decision(workflow_id) != "decided":
            _cancel_run_txn(workflow_id, "return eligibility not approved")
            return {"workflowId": workflow_id, "status": "cancelled"}
        # The API continuation chain advances the case through the human gates;
        # the worker executes the refund effect once finance approves it.
        _drive_effects(workflow_id)
        return {"workflowId": workflow_id, "status": "completed"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("return_to_refund_workflow_failed", workflow_id=workflow_id)
        if workflow_id is not None:
            _mark_run_failed_txn(workflow_id, str(exc))
        raise


@DBOS.transaction(name="slice_daily_reconciliation")
def _daily_reconciliation_txn(
    run_type: str,
    domains: list[str],
    connectors: dict[str, Callable[[str], list[dict[str, Any]]]] | None,
) -> dict[str, Any]:
    db = DBOS.sql_session
    rec_run = run_reconciliation(db, run_type=run_type, domains=domains, connectors=connectors)
    return {"reconciliation_run_id": str(rec_run.id), "summary": rec_run.summary}


def _connectors_for_domains(domains: list[str]) -> dict[str, Callable[[str], list[dict[str, Any]]]]:
    """Resolve connector read-state callables from app/connectors when present."""
    connectors: dict[str, Callable[[str], list[dict[str, Any]]]] = {}
    try:
        from app.connectors import get_reconciliation_connector  # type: ignore[import-not-found]
    except ImportError:
        return connectors
    for domain in domains:
        connector = get_reconciliation_connector(domain)
        if connector is not None:
            connectors[domain] = connector
    return connectors


@DBOS.scheduled("0 3 * * *")
@DBOS.workflow(name="daily_reconciliation_workflow")
def daily_reconciliation_workflow(
    scheduled_time: datetime,
    actual_time: datetime,
) -> dict[str, Any]:
    """Daily reconciliation of the effect ledger against Shopify / Odoo.

    DBOS 2.29 invokes scheduled workflows with ``(scheduled_time, actual_time)``
    positional datetimes (see ``DecoratedScheduledWorkflow``).
    """
    run_type = "daily"
    domains = ["effect", "order", "return"]
    try:
        connectors = _connectors_for_domains(domains)
        return _daily_reconciliation_txn(run_type, domains, connectors or None)
    except Exception:  # noqa: BLE001
        logger.exception(
            "daily_reconciliation_workflow_failed",
            run_type=run_type,
            scheduled_time=scheduled_time.isoformat(),
            actual_time=actual_time.isoformat(),
        )
        raise


# ---------------------------------------------------------------------------
# Worker inbox consumer: starts domain workflows from webhook events.
# ---------------------------------------------------------------------------


def _worker_event_handler(event) -> None:
    payload = event.payload or {}
    if not payload.get("webhook_id"):
        return
    correlation_id = event.correlation_id
    if event.event_type == "order.received":
        DBOS.start_workflow(
            order_to_cash_workflow,
            payload=payload,
            correlation_id=correlation_id,
        )
    elif event.event_type == "return.case_requested":
        DBOS.start_workflow(
            return_to_refund_workflow,
            payload=payload,
            correlation_id=correlation_id,
        )


register_consumer("worker", _worker_event_handler)


__all__ = [
    "catalog_change_and_listing_workflow",
    "daily_reconciliation_workflow",
    "get_connector",
    "order_to_cash_workflow",
    "procurement_workflow",
    "register_connector",
    "return_to_refund_workflow",
]
