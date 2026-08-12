"""Idempotency guarantees of ``accept_command`` (DBOS v2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import IdempotencyConflictError, ValidationError
from app.models.catalog import CatalogRevision
from app.models.effect import EffectLedgerEntry
from app.models.messaging import IdempotencyRecord
from app.models.workflow import WorkflowRun
from app.services.commands import accept_command


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def _command(sku: str) -> dict:
    return {"type": "catalog-revision", "payload": {"sku": sku, "title": "Widget"}}


def test_same_key_and_body_is_replayed_exactly_once(db) -> None:
    results = [
        accept_command(
            db,
            command=_command("SKU-1"),
            idempotency_key="replay-key-1",
        )
        for _ in range(10)
    ]

    assert results[0].replayed is False
    for result in results[1:]:
        assert result.replayed is True
        assert result.workflow_id == results[0].workflow_id
        assert result.status_url == results[0].status_url

    # Exactly one run and one idempotency record; accept-only creates no
    # domain entity and no effect-ledger side effect.
    assert _count(db, WorkflowRun) == 1
    assert _count(db, IdempotencyRecord) == 1
    assert _count(db, CatalogRevision) == 0
    assert _count(db, EffectLedgerEntry) == 0


def test_same_key_different_body_raises_conflict(db) -> None:
    accept_command(db, command=_command("SKU-1"), idempotency_key="conflict-key")
    with pytest.raises(IdempotencyConflictError):
        accept_command(db, command=_command("SKU-2"), idempotency_key="conflict-key")
    assert _count(db, WorkflowRun) == 1


def test_different_keys_are_independent(db) -> None:
    first = accept_command(db, command=_command("SKU-1"), idempotency_key="key-a")
    second = accept_command(db, command=_command("SKU-1"), idempotency_key="key-b")
    assert first.replayed is False
    assert second.replayed is False
    assert first.workflow_id != second.workflow_id
    assert _count(db, WorkflowRun) == 2


def test_accept_without_key_raises(db) -> None:
    with pytest.raises(ValidationError):
        accept_command(db, command=_command("SKU-1"), idempotency_key=None)


def test_unknown_command_type_raises(db) -> None:
    with pytest.raises(ValidationError):
        accept_command(
            db,
            command={"type": "no-such-command", "payload": {"sku": "SKU-1"}},
            idempotency_key=f"key-{uuid.uuid4()}",
        )