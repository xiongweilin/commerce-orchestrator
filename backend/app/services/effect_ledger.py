"""Effect ledger: durable record of every intended external side effect.

Lifecycle: ``planned -> dispatched -> succeeded|failed|outcome_unknown``,
then ``succeeded -> reconciled`` or ``failed|outcome_unknown ->
manual_reconciliation -> reconciled``.  An ``outcome_unknown`` effect may
never be blindly re-dispatched: it must go through the reconciliation path.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.uuid7 import uuid7
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.schemas.events import EFFECT_OPS
from app.services.outbox_inbox import emit_event
from app.services.state_machines import can_transition

logger = get_logger("commerce.effect_ledger")


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


__all__ = ["mark_effect", "record_effect"]
