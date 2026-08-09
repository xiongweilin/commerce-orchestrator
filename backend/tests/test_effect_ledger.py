"""Effect ledger: intent uniqueness, lifecycle transitions, reconciliation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.effect import EffectLedgerEntry
from app.services.effect_ledger import mark_effect, record_effect


def _get(db, intent_id) -> EffectLedgerEntry:
    return db.execute(
        select(EffectLedgerEntry).where(EffectLedgerEntry.intent_id == intent_id)
    ).scalar_one()


def test_record_effect_creates_planned_entry(db) -> None:
    entry = record_effect(
        db,
        target_system="shopify",
        operation="product_publish",
        idempotency_key="k-1",
    )
    assert entry.status.value == "planned"
    assert entry.attempt == 0


def test_same_intent_same_operation_is_idempotent_replay(db) -> None:
    intent_id = uuid.uuid4()
    first = record_effect(
        db,
        intent_id=intent_id,
        target_system="shopify",
        operation="product_publish",
    )
    second = record_effect(
        db,
        intent_id=intent_id,
        target_system="shopify",
        operation="product_publish",
    )
    assert first.id == second.id
    assert len(db.execute(select(EffectLedgerEntry)).scalars().all()) == 1


def test_same_intent_different_operation_conflicts(db) -> None:
    intent_id = uuid.uuid4()
    record_effect(
        db,
        intent_id=intent_id,
        target_system="shopify",
        operation="product_publish",
    )
    with pytest.raises(ConflictError):
        record_effect(
            db,
            intent_id=intent_id,
            target_system="shopify",
            operation="product_update",
        )


def test_unknown_operation_rejected(db) -> None:
    with pytest.raises(ValidationError):
        record_effect(db, target_system="shopify", operation="no_such_op")


def test_planned_to_dispatched_to_succeeded(db) -> None:
    entry = record_effect(db, target_system="odoo", operation="product_create")
    entry = mark_effect(db, entry.intent_id, status="dispatched")
    assert entry.status.value == "dispatched"
    assert entry.attempt == 1
    entry = mark_effect(
        db,
        entry.intent_id,
        status="succeeded",
        remote_reference="42",
        response_hash="abc123",
    )
    assert entry.status.value == "succeeded"
    assert entry.remote_reference == "42"
    assert entry.response_hash == "abc123"


def test_outcome_unknown_cannot_go_back_to_dispatched(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="refund_create")
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(db, entry.intent_id, status="outcome_unknown")
    assert _get(db, entry.intent_id).compensation == "reconciliation"
    with pytest.raises(ConflictError, match="illegal transition"):
        mark_effect(db, entry.intent_id, status="dispatched")


def test_outcome_unknown_moves_through_manual_reconciliation(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(db, entry.intent_id, status="outcome_unknown", error_detail="timeout")
    with pytest.raises(ConflictError):
        # Blindly resolving without the manual path is forbidden.
        mark_effect(db, entry.intent_id, status="reconciled")
    mark_effect(db, entry.intent_id, status="manual_reconciliation")
    entry = mark_effect(db, entry.intent_id, status="reconciled")
    assert entry.status.value == "reconciled"
    assert entry.error_detail == "timeout"


def test_dispatched_effect_cannot_be_reconciled_directly(db) -> None:
    entry = record_effect(db, target_system="odoo", operation="invoice_validate")
    mark_effect(db, entry.intent_id, status="dispatched")
    with pytest.raises(ConflictError, match="illegal transition"):
        mark_effect(db, entry.intent_id, status="reconciled")


def test_failed_effect_retry_and_escalation(db) -> None:
    entry = record_effect(db, target_system="odoo", operation="po_confirm")
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(db, entry.intent_id, status="failed", error_detail="boom")
    entry = mark_effect(db, entry.intent_id, status="dispatched")
    assert entry.attempt == 2
    mark_effect(db, entry.intent_id, status="failed", error_detail="boom again")
    mark_effect(db, entry.intent_id, status="manual_reconciliation")
    assert _get(db, entry.intent_id).status.value == "manual_reconciliation"


def test_mark_unknown_status_and_missing_entry(db) -> None:
    with pytest.raises(NotFoundError):
        mark_effect(db, uuid.uuid4(), status="succeeded")
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    with pytest.raises(ValidationError):
        mark_effect(db, entry.intent_id, status="no_such_status")


def test_response_hash_recorded_on_success(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    mark_effect(db, entry.intent_id, status="dispatched")
    mark_effect(
        db,
        entry.intent_id,
        status="succeeded",
        remote_reference="gid://shopify/Product/1",
        response_hash="sha256-demo",
    )
    row = _get(db, entry.intent_id)
    assert row.response_hash == "sha256-demo"
    assert row.remote_reference == "gid://shopify/Product/1"
