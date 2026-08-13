"""Effect ledger: durable record of every intended external side effect.

Lifecycle: ``planned -> dispatched -> succeeded|failed|outcome_unknown``,
then ``succeeded -> reconciled`` or ``failed|outcome_unknown ->
manual_reconciliation -> reconciled``.  An ``outcome_unknown`` effect may
never be blindly re-dispatched: it must go through the reconciliation path.

P7 WP5 adds the typed execution seam on top of the ledger:

- :func:`mark_dispatched` / :func:`apply_outcome` — atomic ledger
  transitions the DBOS workflow v2 calls from DBOS transactions.
- :func:`execute_effect` — runs the adapter and returns the typed
  :class:`EffectExecutionOutcome` (never string-inferred).
- :func:`can_retry_effect` — only ``Failed(retryable=True)`` may be
  retried, bounded to :data:`MAX_EFFECT_RETRY_ATTEMPTS`.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select

from app.connectors import registry
from app.connectors.base import (
    ChannelConnector,
    ConnectorError,
    EffectResult,
    OutcomeUnknownError,
    truncate,
)
from app.core.errors import (
    ConflictError,
    ExternalSystemError,
    NotFoundError,
    RetryableEffectError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.uuid7 import uuid7
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.schemas.effects import (
    ERROR_EXPECTED_CONFLICT,
    ERROR_OUTCOME_UNKNOWN,
    ERROR_REMOTE_ERROR,
    ERROR_RETRYABLE,
    EffectExecutionOutcome,
    EffectExecutionRequest,
    EffectFailed,
    EffectOutcomeUnknown,
    EffectSucceeded,
    validate_effect_parameter_coverage,
)
from app.schemas.events import EFFECT_OPS
from app.services.outbox_inbox import emit_event
from app.services.state_machines import can_transition

logger = get_logger("commerce.effect_ledger")

MAX_EFFECT_RETRY_ATTEMPTS = 3
"""Hard bound for retrying ``Failed(retryable=True)`` effects (plan 二.4)."""

_CREDIT_NOTE_EFFECT_OPS = frozenset({"odoo.credit_note_create", "odoo.credit_note_validate"})
_INVENTORY_EFFECT_OPS = frozenset(
    {"odoo.stock_move_create", "odoo.picking_validate", "odoo.receive_transfer"}
)

_OP_METHOD: dict[str, str] = {
    "shopify.refund_create": "create_refund",
    "shopify.product_update": "update_product",
    "shopify.product_publish": "publish_product",
    "shopify.fulfillment_create": "create_fulfillment",
    "odoo.product_create": "create_product",
    "odoo.product_update": "update_product",
    "odoo.sale_order_create": "create_sale_order",
    "odoo.sale_order_confirm": "confirm_sale_order",
    "odoo.stock_move_create": "create_stock_move",
    "odoo.picking_create": "create_picking",
    "odoo.picking_validate": "validate_picking",
    "odoo.receive_transfer": "receive_transfer",
    "odoo.invoice_create": "create_invoice",
    "odoo.invoice_validate": "validate_invoice",
    "odoo.credit_note_create": "create_credit_note",
    "odoo.credit_note_validate": "validate_credit_note",
    "odoo.po_create": "create_po",
    "odoo.po_confirm": "confirm_po",
    "odoo.bill_create": "create_bill",
}
"""Every ``EFFECT_OPS`` operation dispatches to one adapter method."""


def effect_transition_context(operation: str) -> dict[str, Any]:
    """Attest the ledger money/inventory invariants for an operation.

    Credit-note effects are only legal against posted invoices; inventory
    effects must name their change source.  Pass the full
    ``<system>.<operation>`` name (e.g. ``"odoo.credit_note_validate"``).
    """
    ctx: dict[str, Any] = {}
    if operation in _CREDIT_NOTE_EFFECT_OPS:
        ctx["invoice_posted"] = True
    if operation in _INVENTORY_EFFECT_OPS:
        ctx["inventory_change_source"] = "stock_move"
    return ctx


def validate_effect_dispatch_coverage() -> None:
    """Fail fast when ``EFFECT_OPS`` and the adapter dispatch drift apart."""
    validate_effect_parameter_coverage()
    missing = EFFECT_OPS - set(_OP_METHOD)
    if missing:
        raise RuntimeError(f"missing adapter dispatch for effect operations: {sorted(missing)}")


def record_effect(
    db,
    *,
    intent_id: uuid.UUID | None = None,
    target_system: str,
    operation: str,
    idempotency_key: str | None = None,
    approval_ref: uuid.UUID | None = None,
    request_hash: str | None = None,
) -> EffectLedgerEntry:
    """Record an intended external side effect as ``planned``.

    ``operation`` is the operation name without the system prefix
    (``target_system`` + ``.`` + ``operation`` must be in ``EFFECT_OPS``).
    Re-recording the same ``intent_id`` with the same operation is an
    idempotent replay; a different operation raises :class:`ConflictError`.
    """
    full_operation = f"{target_system}.{operation}"
    if full_operation not in EFFECT_OPS:
        raise ValidationError(f"unknown effect operation: {full_operation}")

    intent_id = intent_id or uuid7()
    existing = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == intent_id)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.target_system != target_system or existing.operation != operation:
            raise ConflictError(
                f"intent_id {intent_id} already recorded with a different operation"
            )
        return existing

    entry = EffectLedgerEntry(
        intent_id=intent_id,
        target_system=target_system,
        operation=operation,
        idempotency_key=idempotency_key,
        attempt=0,
        approval_ref=approval_ref,
        status=EffectStatus.PLANNED,
        request_hash=request_hash,
    )
    db.add(entry)
    emit_event(
        db,
        event_type="effect.planned",
        aggregate_type="effect",
        aggregate_id=str(intent_id),
        producer="effect",
        payload={
            "intent_id": str(intent_id),
            "operation": full_operation,
            "idempotency_key": idempotency_key,
        },
    )
    db.flush()
    return entry


def mark_dispatched(
    db,
    intent_id: uuid.UUID,
    *,
    context: Mapping[str, Any] | None = None,
) -> EffectLedgerEntry:
    """Atomic ``planned -> dispatched`` transition (attempt += 1).

    Called from the DBOS transaction that starts effect execution. Replays of
    the same DBOS transaction are idempotent at the DBOS layer; a *different*
    dispatch attempt is only legal from ``failed`` (see the state machine,
    bounded by :data:`MAX_EFFECT_RETRY_ATTEMPTS`). ``outcome_unknown``
    effects can never be re-dispatched.
    """
    return mark_effect(db, intent_id, status="dispatched", context=context)


def apply_outcome(
    db,
    intent_id: uuid.UUID,
    outcome: EffectExecutionOutcome,
    *,
    context: Mapping[str, Any] | None = None,
) -> EffectLedgerEntry:
    """Atomically persist a typed effect outcome on the ledger.

    Maps :class:`EffectSucceeded` / :class:`EffectFailed` /
    :class:`EffectOutcomeUnknown` to the corresponding terminal ledger
    status.  Re-applying the same terminal outcome is an idempotent replay;
    applying a conflicting outcome raises :class:`ConflictError`.
    ``outcome_unknown`` is recorded with ``compensation="reconciliation"`` so
    the workflow routes the run to ``needs_reconciliation`` (never retried).
    """
    entry = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == intent_id)
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"effect ledger entry {intent_id} not found")
    current = entry.status.value

    if isinstance(outcome, EffectSucceeded):
        if current == "succeeded":
            if outcome.remote_reference and entry.remote_reference not in (
                None,
                outcome.remote_reference,
            ):
                raise ConflictError(
                    f"effect {intent_id} already succeeded with reference "
                    f"{entry.remote_reference!r}; refusing {outcome.remote_reference!r}"
                )
            return entry
        if current in {"failed", "outcome_unknown"}:
            raise ConflictError(
                f"effect {intent_id} already {current}; refusing to apply succeeded"
            )
        return mark_effect(
            db,
            intent_id,
            status="succeeded",
            remote_reference=outcome.remote_reference,
            response_hash=outcome.response_hash,
            context=context,
        )
    if isinstance(outcome, EffectFailed):
        if current == "failed":
            return entry
        if current in {"succeeded", "outcome_unknown"}:
            raise ConflictError(f"effect {intent_id} already {current}; refusing to apply failed")
        return mark_effect(
            db,
            intent_id,
            status="failed",
            error_detail=outcome.detail,
            context=context,
        )
    # EffectOutcomeUnknown — never auto-re-dispatched.
    if current == "outcome_unknown":
        return entry
    if current in {"succeeded", "failed"}:
        raise ConflictError(
            f"effect {intent_id} already {current}; refusing to apply outcome_unknown"
        )
    return mark_effect(
        db,
        intent_id,
        status="outcome_unknown",
        error_detail=outcome.detail,
        context=context,
    )


def _dispatch_kwargs(method: Callable[..., Any], request: EffectExecutionRequest) -> dict[str, Any]:
    """Build adapter kwargs from the typed parameters (fail-closed)."""
    signature = inspect.signature(method)
    parameters = set(signature.parameters)
    accepts_var_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )
    kwargs: dict[str, Any] = request.parameters.model_dump(exclude={"operation"})
    if "idempotency_key" in parameters or accepts_var_kwargs:
        kwargs["idempotency_key"] = request.idempotency_key
    if "intent_id" in parameters or accepts_var_kwargs:
        kwargs["intent_id"] = str(request.intent_id)
    if not accepts_var_kwargs:
        unknown = set(kwargs) - parameters
        if unknown:
            raise ConnectorError(
                f"effect parameter mismatch for {request.operation}: "
                f"adapter does not accept {sorted(unknown)}"
            )
    return kwargs


def execute_effect(
    request: EffectExecutionRequest,
    *,
    connector_provider: Callable[[str], ChannelConnector] = registry.get_connector,
) -> EffectExecutionOutcome:
    """Execute one effect via its adapter and return the typed outcome.

    Fail-closed mapping (no string inference, no "timeout" guessing):

    - :class:`OutcomeUnknownError` -> :class:`EffectOutcomeUnknown`
      (ambiguous remote state — never blind-retried).
    - :class:`RetryableEffectError` -> ``EffectFailed(retryable=True)``.
    - any other :class:`ExternalSystemError` (incl. ``OdooApiError``) ->
      ``EffectFailed(retryable=False)`` (definitive, not applied).
    - :class:`ConnectorError` (adapter/operation not configured)
      **propagates** — a startup configuration error: the worker must not be
      ready and the effect must never be marked ``succeeded``.

    Adapter methods keep returning the v1 :class:`EffectResult` (backward
    compatible); this seam converts them into the typed outcome, carrying the
    read-back idempotency ``replayed`` flag through.
    """
    if request.operation not in _OP_METHOD:
        raise ConnectorError(f"no adapter dispatch for effect operation {request.operation!r}")
    system = request.operation.split(".", 1)[0]
    connector = connector_provider(system)
    method = getattr(connector, _OP_METHOD[request.operation])
    try:
        result = method(**_dispatch_kwargs(method, request))
    except OutcomeUnknownError as exc:
        return EffectOutcomeUnknown(error_code=ERROR_OUTCOME_UNKNOWN, detail=truncate(exc.detail))
    except RetryableEffectError as exc:
        return EffectFailed(
            error_code=ERROR_RETRYABLE,
            detail=truncate(exc.detail),
            retryable=True,
        )
    except ExternalSystemError as exc:
        return EffectFailed(
            error_code=ERROR_REMOTE_ERROR,
            detail=truncate(exc.detail),
            retryable=False,
        )
    if isinstance(result, EffectResult):
        if result.ok:
            return EffectSucceeded(
                remote_reference=result.remote_reference,
                response_hash=result.response_hash,
                replayed=result.replayed,
            )
        return EffectFailed(
            error_code=ERROR_EXPECTED_CONFLICT,
            detail=result.error or "expected conflict",
            retryable=False,
            response_hash=result.response_hash,
        )
    raise ConnectorError(
        f"adapter {type(connector).__name__}.{request.operation} returned "
        f"unsupported result type {type(result).__name__}"
    )


def can_retry_effect(
    outcome: EffectExecutionOutcome,
    *,
    attempts: int,
    max_attempts: int = MAX_EFFECT_RETRY_ATTEMPTS,
) -> bool:
    """Only ``Failed(retryable=True)`` may be retried, bounded to 3 attempts."""
    return (
        isinstance(outcome, EffectFailed)
        and outcome.retryable
        and int(attempts) < int(max_attempts)
    )


def mark_effect(
    db,
    intent_id: uuid.UUID,
    *,
    status: str,
    remote_reference: str | None = None,
    response_hash: str | None = None,
    error_detail: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> EffectLedgerEntry:
    """Transition an effect ledger entry to a new status.

    Status transitions are validated against the EffectLedgerEntry state
    machine.  ``outcome_unknown`` cannot be blindly retried (no transition to
    ``dispatched``); it must move to ``reconciled`` only after manual review.
    """
    entry = db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == intent_id)
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"effect ledger entry {intent_id} not found")
    if status not in {s.value for s in EffectStatus}:
        raise ValidationError(f"unknown effect status: {status}")

    from_state = entry.status.value
    transition_context: dict[str, Any] = {
        "operation": f"{entry.target_system}.{entry.operation}",
        "attempts": entry.attempt,
    }
    if context:
        transition_context.update(context)
    ok, reason = can_transition("EffectLedgerEntry", from_state, status, transition_context)
    if not ok:
        raise ConflictError(f"effect {intent_id} {from_state} -> {status}: {reason}")

    entry.status = EffectStatus(status)
    if status == "dispatched":
        entry.attempt += 1
    if remote_reference is not None:
        entry.remote_reference = remote_reference
    if response_hash is not None:
        entry.response_hash = response_hash
    if error_detail is not None:
        entry.error_detail = error_detail
    if status == "outcome_unknown" and entry.compensation is None:
        entry.compensation = "reconciliation"

    emit_event(
        db,
        event_type=f"effect.{status}",
        aggregate_type="effect",
        aggregate_id=str(intent_id),
        producer="effect",
        payload={
            "intent_id": str(intent_id),
            "operation": f"{entry.target_system}.{entry.operation}",
            "remote_reference": remote_reference,
            "error_detail": error_detail,
        },
    )
    db.flush()
    return entry


__all__ = [
    "MAX_EFFECT_RETRY_ATTEMPTS",
    "apply_outcome",
    "can_retry_effect",
    "effect_transition_context",
    "execute_effect",
    "mark_dispatched",
    "mark_effect",
    "record_effect",
    "validate_effect_dispatch_coverage",
]
