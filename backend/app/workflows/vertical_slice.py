"""DBOS v1 legacy workflows (in-flight runs compatibility adapter, P7 二.2).

P7 WP4 replaced the v1 inline continuation + this slice with one DBOS v2
definition set (``app.workflows.definitions``).  This module is kept only to
finish in-flight ``legacy_inline`` / webhook-driven v1 runs: the workflow
functions keep their names and signatures so DBOS recovery of already-started
executions keeps working.  Effect execution no longer uses a stub or a
mutable connector registry — it goes through the same WP5 typed seam as v2
(``app.workflows.effect_execution``) and fails closed when the adapter cannot
produce a typed request.

This module intentionally imports ``dbos`` at module import time (decorators
must be applied while the module is loaded).  ``app.services`` never imports
this module, so tests do not require a live DBOS runtime.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from dbos import DBOS
from pydantic import TypeAdapter
from sqlalchemy import select

from app.core.logging import get_logger
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.returns import ReturnCase, ReturnStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemStatus,
)
from app.schemas.effects import EffectExecutionOutcome, EffectExecutionRequest
from app.services.approvals import create_work_item
from app.services.commands import advance_entity, canonical_hash
from app.services.effect_ledger import record_effect
from app.services.outbox_inbox import emit_event
from app.services.reconciliation import run_reconciliation
from app.workflows.effect_execution import (
    apply_effect_outcome,
    build_effect_execution_request,
    execute_effect_seam,
)

logger = get_logger("commerce.workflows")

DEFAULT_APPROVAL_TIMEOUT_DAYS = 30
DEFAULT_POLL_SECONDS = 60


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
                "operation": f"{effect.target_system}.{effect.operation}",
                "idempotency_key": effect.idempotency_key,
                "request_hash": effect.request_hash,
            }
            for effect in effects
        ],
    }


@DBOS.transaction(name="slice_build_effect_request")
def _build_effect_request_txn(workflow_id: str, effect: dict[str, Any]) -> dict[str, Any]:
    """Build the WP5 typed request for a planned ledger row (fail-closed)."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        raise ValueError(f"workflow run {workflow_id} not found")
    request = build_effect_execution_request(db, run, effect)
    return request.model_dump(mode="json")


@DBOS.step(name="slice_execute_effect")
def _execute_effect_step(request_json: dict[str, Any]) -> dict[str, Any]:
    """Execute one effect through the WP5 typed seam (at-least-once)."""
    request = EffectExecutionRequest.model_validate(request_json)
    outcome = execute_effect_seam(request)
    return {
        "effect_id": str(request.intent_id),
        "operation": request.operation,
        "outcome": outcome.model_dump(mode="json"),
    }


@DBOS.transaction(name="slice_apply_effect_outcome")
def _apply_effect_outcome_txn(workflow_id: str, step_result: dict[str, Any]) -> None:
    """Mark dispatched + persist the typed outcome + finalize the domain."""
    from app.services.effect_ledger import effect_transition_context, mark_dispatched

    db = DBOS.sql_session
    effect_id = uuid.UUID(step_result["effect_id"])
    operation = str(step_result.get("operation", ""))
    outcome = TypeAdapter(EffectExecutionOutcome).validate_python(step_result["outcome"])
    entry = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == effect_id)
    ).scalar_one_or_none()
    if entry is not None:
        mark_dispatched(db, effect_id, context=effect_transition_context(operation))
    apply_effect_outcome(
        db,
        workflow_id=workflow_id,
        effect_id=effect_id,
        operation=operation,
        outcome=outcome,
    )


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
            request_json = _build_effect_request_txn(workflow_id, effect)
            step_result = _execute_effect_step(request_json)
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
                    request_json = _build_effect_request_txn(workflow_id, effect)
                    step_result = _execute_effect_step(request_json)
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
    request_json = _build_effect_request_txn(
        workflow_id,
        {
            "effect_id": effect_id,
            "target_system": target_system,
            "operation": operation,
            "idempotency_key": f"{workflow_id}:{operation}",
            "request_hash": canonical_hash({"correlation_id": correlation_id}),
        },
    )
    step_result = _execute_effect_step(request_json)
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


__all__ = [
    "catalog_change_and_listing_workflow",
    "daily_reconciliation_workflow",
    "order_to_cash_workflow",
    "procurement_workflow",
    "return_to_refund_workflow",
]
