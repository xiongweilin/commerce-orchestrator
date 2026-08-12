"""DBOS v2 workflow definitions — the single orchestration mainline (P7 二.1).

Every new command (``catalog-revision``, ``listing-publication``,
``procurement``, ``return``, ``reconciliation``) is accepted by
``app.services.commands.accept_command`` as a run with
``orchestration_engine='dbos'`` and ``workflow_version=2``; the worker relay
starts the definition resolved from the ``(workflow_type, workflow_version)``
registry with ``SetWorkflowID(str(run_id))``.

The definitions compose the *same* pure state machines and domain
continuations the legacy path uses (``app.services.commands`` entries and the
``app.services.approvals`` next-step registry) — no second set of transition
rules.  Human gates use ``DBOS.recv`` with a 30-day timeout; effect execution
goes through WP5's typed seam (``app.workflows.effect_execution``).

Status flow (plan 二.1): ``accepted -> running -> awaiting_approval -> running
-> completed | needs_reconciliation | failed | cancelled``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from dbos import DBOS
from pydantic import TypeAdapter
from sqlalchemy import select

from app.config import get_settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemStatus,
)
from app.schemas.effects import EffectExecutionOutcome, EffectExecutionRequest
from app.services.approvals import apply_domain_continuation
from app.services.commands import COMMAND_HANDLERS
from app.services.outbox_inbox import emit_event
from app.workflows.effect_execution import (
    apply_effect_outcome,
    build_effect_execution_request,
    execute_effect_seam,
    normalize_outcome,
)
from app.workflows.metrics import (
    record_effect_attempt,
    record_effect_outcome_unknown,
    record_workflow_terminal,
)

logger = get_logger("commerce.workflows")

APPROVAL_TIMEOUT_SECONDS = 30 * 24 * 3600
"""One approval gate may wait up to 30 days (plan 二.3)."""

MAX_GATES_PER_RUN = 16
"""Safety bound on the human-gate loop; exceeding it fails the run closed."""

FINAL_STATUSES = frozenset(
    {
        WorkflowRunStatus.COMPLETED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
    }
)

WORKFLOW_DEFINITIONS: dict[tuple[str, int], Callable[..., Any]] = {}
"""``(workflow_type, workflow_version)`` -> DBOS workflow function."""


def register_definition(workflow_type: str, workflow_version: int) -> Callable:
    """Decorator registering a DBOS workflow under ``(type, version)``."""

    def _decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        WORKFLOW_DEFINITIONS[(workflow_type, workflow_version)] = fn
        return fn

    return _decorate


def resolve_definition(workflow_type: str, workflow_version: int) -> Callable[..., Any]:
    """Resolve the definition for a ``(workflow_type, workflow_version)`` pair."""
    try:
        return WORKFLOW_DEFINITIONS[(workflow_type, workflow_version)]
    except KeyError:
        raise ValueError(
            f"no workflow definition for ({workflow_type!r}, {workflow_version!r})"
        ) from None


def definition_names() -> list[str]:
    """Registered definition keys as ``type@version`` strings (ops tooling)."""
    return [f"{key[0]}@{key[1]}" for key in sorted(WORKFLOW_DEFINITIONS)]


# ---------------------------------------------------------------------------
# DBOS transactions / steps
# ---------------------------------------------------------------------------


@DBOS.transaction(name="wf2_start")
def _start_txn(workflow_id: str) -> None:
    """Run the domain entry for an accepted run (once; recovery-safe)."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        raise ValueError(f"workflow run {workflow_id} not found")
    if run.status != WorkflowRunStatus.ACCEPTED:
        return  # already started (recovery replay)
    run.status = WorkflowRunStatus.RUNNING
    run.started_at = utc_now()
    run.version += 1
    db.flush()
    handler = COMMAND_HANDLERS.get(run.workflow_type)
    if handler is None:
        raise ValueError(f"no domain entry for workflow type {run.workflow_type!r}")
    # The domain entry creates the first work item with
    # expected_version=run.version (post-bump) and sets awaiting_approval.
    handler(db, run, run.input_json or {}, run.initiated_by_user_id, run.correlation_id)


def _normalize_terminal(db, run: WorkflowRun) -> None:
    """Set finished_at / bump version for runs finalized by a continuation."""
    if run.status in FINAL_STATUSES and run.finished_at is None:
        run.finished_at = utc_now()
        run.version += 1
        record_workflow_terminal(run.workflow_type, run.status.value)
        db.flush()


@DBOS.transaction(name="wf2_snapshot")
def _snapshot_txn(workflow_id: str) -> dict[str, Any]:
    """Snapshot of a run: status, pending items and planned effects."""
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
    # Delay terminal normalisation until planned effects have been executed:
    # v1 continuations reused by the v2 driver may mark the run completed
    # (e.g. the closing gate) while effects are still recorded as planned.
    if not effects:
        _normalize_terminal(db, run)
    return {
        "status": run.status.value,
        "workflow_type": run.workflow_type,
        "pending_items": [{"work_item_id": str(item.id)} for item in pending],
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


@DBOS.transaction(name="wf2_apply_decision")
def _apply_decision_txn(workflow_id: str, decision_payload: dict[str, Any]) -> None:
    """Apply a received decision: bump version, run the domain continuation."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        raise ValueError(f"workflow run {workflow_id} not found")
    item = db.get(WorkItem, uuid.UUID(decision_payload["work_item_id"]))
    if item is None:
        raise ValueError(f"work item {decision_payload['work_item_id']} not found")
    decision = str(decision_payload["decision"])
    actor = uuid.UUID(str(decision_payload["actor_user_id"]))
    reason = decision_payload.get("reason")
    # Bump before the continuation so the next work item snapshots the new
    # version (CAS: expectedWorkflowVersion == run.version at decision time).
    run.version += 1
    apply_domain_continuation(
        db,
        run=run,
        item=item,
        user_id=actor,
        decision=decision,
        reason=reason,
    )
    db.flush()


@DBOS.transaction(name="wf2_dispatch_effect")
def _dispatch_effect_txn(workflow_id: str, effect: dict[str, Any]) -> dict[str, Any]:
    """Build the typed effect request and mark the ledger ``dispatched``."""
    from app.services.effect_ledger import effect_transition_context, mark_dispatched

    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None:
        raise ValueError(f"workflow run {workflow_id} not found")
    operation = str(effect["operation"])
    # Fail-closed: a request that cannot be built raises before any dispatch.
    request = build_effect_execution_request(db, run, effect)
    mark_dispatched(
        db,
        uuid.UUID(effect["effect_id"]),
        context=effect_transition_context(operation),
    )
    db.flush()
    return request.model_dump(mode="json")


@DBOS.step(name="wf2_execute_effect")
def _execute_effect_step(request_json: dict[str, Any]) -> dict[str, Any]:
    """Execute one effect through the WP5 typed seam (at-least-once)."""
    request = EffectExecutionRequest.model_validate(request_json)
    outcome = execute_effect_seam(request)
    return outcome.model_dump(mode="json")


@DBOS.transaction(name="wf2_apply_effect_outcome")
def _apply_effect_outcome_txn(
    workflow_id: str,
    effect_id: str,
    operation: str,
    outcome_json: dict[str, Any],
) -> None:
    """Persist the typed outcome and advance the domain after success."""
    db = DBOS.sql_session
    outcome = TypeAdapter(EffectExecutionOutcome).validate_python(outcome_json)
    apply_effect_outcome(
        db,
        workflow_id=workflow_id,
        effect_id=uuid.UUID(effect_id),
        operation=operation,
        outcome=outcome,
    )


@DBOS.transaction(name="wf2_complete")
def _complete_txn(workflow_id: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None or run.status in FINAL_STATUSES:
        return
    run.status = WorkflowRunStatus.COMPLETED
    run.result_json = {
        "workflowId": workflow_id,
        "status": "completed",
        "statusUrl": f"/v1/workflows/{workflow_id}",
    }
    run.finished_at = utc_now()
    run.version += 1
    emit_event(
        db,
        event_type="workflow.completed",
        aggregate_type="workflow",
        aggregate_id=workflow_id,
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": workflow_id, "workflow_type": run.workflow_type},
    )
    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.COMPLETED.value)
    db.flush()


@DBOS.transaction(name="wf2_cancel")
def _cancel_txn(workflow_id: str, reason: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None or run.status in FINAL_STATUSES:
        return
    run.status = WorkflowRunStatus.CANCELLED
    run.result_json = {"cancelled": True, "reason": reason}
    run.finished_at = utc_now()
    run.version += 1
    emit_event(
        db,
        event_type="workflow.cancelled",
        aggregate_type="workflow",
        aggregate_id=workflow_id,
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": workflow_id, "reason": reason},
    )
    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.CANCELLED.value)
    db.flush()


@DBOS.transaction(name="wf2_fail")
def _fail_txn(workflow_id: str, error: str) -> None:
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None or run.status in FINAL_STATUSES:
        return
    run.status = WorkflowRunStatus.FAILED
    run.error = error[:2000]
    run.finished_at = utc_now()
    run.version += 1
    emit_event(
        db,
        event_type="workflow.failed",
        aggregate_type="workflow",
        aggregate_id=workflow_id,
        correlation_id=run.correlation_id,
        producer="workflow",
        payload={"workflow_id": workflow_id, "error": error[:500]},
    )
    record_workflow_terminal(run.workflow_type, WorkflowRunStatus.FAILED.value)
    db.flush()


@DBOS.transaction(name="wf2_needs_reconciliation")
def _needs_reconciliation_txn(workflow_id: str, reason: str) -> None:
    """Pause the run for reconciliation (not a terminal failure; resumable)."""
    db = DBOS.sql_session
    run = db.get(WorkflowRun, uuid.UUID(workflow_id))
    if run is None or run.status in FINAL_STATUSES:
        return
    run.status = WorkflowRunStatus.NEEDS_RECONCILIATION
    run.result_json = {"needsReconciliation": True, "reason": reason}
    run.version += 1
    db.flush()


def _drive_effects(
    workflow_id: str,
    effects: list[dict[str, Any]],
    *,
    max_retries: int,
) -> str:
    """Execute planned effects in order; return the driver outcome.

    Returns ``"effects_done"`` after all succeed, or the run status the
    workflow must settle into: ``needs_reconciliation`` (ambiguous remote
    state — never blind-retried) or ``failed`` (definitive / exhausted).
    """
    for effect in effects:
        operation = str(effect["operation"])
        effect_id = str(effect["effect_id"])
        target_system, _, op = operation.partition(".")
        attempts = 0
        while True:
            attempts += 1
            request_json = _dispatch_effect_txn(workflow_id, effect)
            outcome_json = _execute_effect_step(request_json)
            normalized = normalize_outcome(
                TypeAdapter(EffectExecutionOutcome).validate_python(outcome_json)
            )
            status = normalized["status"]
            record_effect_attempt(target_system=target_system, operation=op, status=status)
            if (
                status == "failed"
                and normalized["retryable"]
                and attempts < max_retries
            ):
                # Ledger: dispatched -> failed (attempt recorded), then re-dispatch.
                from app.schemas.effects import EffectFailed

                _mark_failed_retry_txn(
                    effect_id,
                    operation,
                    EffectFailed(
                        error_code=normalized["error_code"] or "retryable_error",
                        detail=normalized["error"] or "retryable effect failure",
                        retryable=True,
                    ),
                )
                continue
            _apply_effect_outcome_txn(workflow_id, effect_id, operation, outcome_json)
            if status == "outcome_unknown":
                record_effect_outcome_unknown(target_system=target_system, operation=op)
                _needs_reconciliation_txn(
                    workflow_id,
                    f"effect {operation} outcome unknown; reconcile before continuing",
                )
                return "needs_reconciliation"
            if status == "failed":
                _fail_txn(
                    workflow_id,
                    f"effect {operation} failed definitively: "
                    f"{(normalized['error'] or '')[:300]}",
                )
                return "failed"
            break
    return "effects_done"


@DBOS.transaction(name="wf2_mark_failed_retry")
def _mark_failed_retry_txn(effect_id: str, operation: str, outcome: Any) -> None:
    from app.services.effect_ledger import apply_outcome, effect_transition_context

    db = DBOS.sql_session
    apply_outcome(
        db,
        uuid.UUID(effect_id),
        outcome,
        context=effect_transition_context(operation),
    )


def _final_result(workflow_id: str, status: str) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "status": status,
        "statusUrl": f"/v1/workflows/{workflow_id}",
    }


def _drive_v2(
    workflow_id: str,
    *,
    max_retries: int,
    max_gates: int = MAX_GATES_PER_RUN,
) -> dict[str, Any]:
    """Generic v2 driver: gates via DBOS.recv, effects via the typed seam."""
    _start_txn(workflow_id)
    for _ in range(max_gates):
        state = _snapshot_txn(workflow_id)
        status = state["status"]
        # Effects take priority over a terminal status: a v1 continuation may
        # have recorded planned effects and marked the run completed in the
        # same decision transaction (e.g. the closing gate). Execute them
        # before honouring the terminal state so an effect never stays
        # ``planned`` in a completed run (plan 二.4 execution order).
        if state["planned_effects"]:
            outcome = _drive_effects(
                workflow_id,
                state["planned_effects"],
                max_retries=max_retries,
            )
            if outcome != "effects_done":
                return _final_result(workflow_id, outcome)
            continue
        if status in ("completed", "failed", "cancelled", "needs_reconciliation"):
            return _final_result(workflow_id, status)
        if status == "not_found":
            raise ValueError(f"workflow run {workflow_id} not found")
        if state["pending_items"]:
            item_id = state["pending_items"][0]["work_item_id"]
            decision = DBOS.recv(topic=str(item_id), timeout_seconds=APPROVAL_TIMEOUT_SECONDS)
            if decision is None:
                _cancel_txn(workflow_id, "approval timed out")
                return _final_result(workflow_id, "cancelled")
            _apply_decision_txn(workflow_id, dict(decision))
            continue
        _complete_txn(workflow_id)
        return _final_result(workflow_id, "completed")
    _fail_txn(workflow_id, "workflow gate limit exceeded")
    return _final_result(workflow_id, "failed")


def _run_definition(workflow_id: str, workflow_type: str) -> dict[str, Any]:
    """Wrap the driver: any uncaught error fails the run closed (no silent ok)."""
    max_retries = get_settings().effect_max_retries
    try:
        return _drive_v2(workflow_id, max_retries=max_retries)
    except Exception as exc:  # noqa: BLE001 - uncaught -> status failed
        logger.exception("workflow_v2_failed", workflow_type=workflow_type, workflow_id=workflow_id)
        try:
            _fail_txn(workflow_id, str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("workflow_v2_fail_marker_failed", workflow_id=workflow_id)
        return _final_result(workflow_id, "failed")


# ---------------------------------------------------------------------------
# DBOS v2 definitions (registered under (workflow_type, 2))
# ---------------------------------------------------------------------------


@register_definition("catalog-revision", 2)
@DBOS.workflow(name="catalog_revision_workflow_v2")
def catalog_revision_workflow_v2(*, workflow_id: str) -> dict[str, Any]:
    """candidate -> approval -> product publish -> listing active -> official."""
    return _run_definition(workflow_id, "catalog-revision")


@register_definition("listing-publication", 2)
@DBOS.workflow(name="listing_publication_workflow_v2")
def listing_publication_workflow_v2(*, workflow_id: str) -> dict[str, Any]:
    """draft -> approval -> publishing -> active."""
    return _run_definition(workflow_id, "listing-publication")


@register_definition("procurement", 2)
@DBOS.workflow(name="procurement_workflow_v2")
def procurement_workflow_v2(*, workflow_id: str) -> dict[str, Any]:
    """demand -> PO approval -> Odoo PO -> receive -> bill -> close."""
    return _run_definition(workflow_id, "procurement")


@register_definition("return", 2)
@DBOS.workflow(name="return_to_refund_workflow_v2")
def return_to_refund_workflow_v2(*, workflow_id: str) -> dict[str, Any]:
    """case -> eligibility -> goods received -> disposition -> credit note ->
    refund (durable approval at every gate)."""
    return _run_definition(workflow_id, "return")


@register_definition("reconciliation", 2)
@DBOS.workflow(name="reconciliation_workflow_v2")
def reconciliation_workflow_v2(*, workflow_id: str) -> dict[str, Any]:
    """Run a reconciliation and complete (no human gates or effects)."""
    return _run_definition(workflow_id, "reconciliation")


__all__ = [
    "APPROVAL_TIMEOUT_SECONDS",
    "MAX_GATES_PER_RUN",
    "WORKFLOW_DEFINITIONS",
    "catalog_revision_workflow_v2",
    "definition_names",
    "listing_publication_workflow_v2",
    "procurement_workflow_v2",
    "reconciliation_workflow_v2",
    "register_definition",
    "resolve_definition",
    "return_to_refund_workflow_v2",
]
