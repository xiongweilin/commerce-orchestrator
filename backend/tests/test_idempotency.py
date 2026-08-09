"""Idempotency guarantees of ``dispatch_command``."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import IdempotencyConflictError
from app.models.catalog import CatalogRevision
from app.models.effect import EffectLedgerEntry
from app.models.messaging import IdempotencyRecord
from app.models.workflow import WorkflowRun
from app.services.commands import dispatch_command


def _count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_same_key_and_body_is_replayed_exactly_once(db) -> None:
    payload = {"sku": "SKU-1", "title": "Widget", "proposed": {"price": "9.99"}}
    results = [
        dispatch_command(
            db,
            scope="catalog",
            key="replay-key-1",
            command_type="catalog-revision",
            payload=payload,
        )
        for _ in range(10)
    ]

    assert results[0]["replayed"] is False
    for result in results[1:]:
        assert result["replayed"] is True
        assert result["workflowId"] == results[0]["workflowId"]
        assert result["statusUrl"] == results[0]["statusUrl"]

    # Exactly one run, one idempotency record and one domain entity.
    assert _count(db, WorkflowRun) == 1
    assert _count(db, IdempotencyRecord) == 1
    assert _count(db, CatalogRevision) == 1
    # No effect-ledger side effect is duplicated by the replays.
    assert _count(db, EffectLedgerEntry) == 0


def test_same_key_different_body_raises_conflict(db) -> None:
    dispatch_command(
        db,
        scope="catalog",
        key="conflict-key",
        command_type="catalog-revision",
        payload={"sku": "SKU-1"},
    )
    with pytest.raises(IdempotencyConflictError):
        dispatch_command(
            db,
            scope="catalog",
            key="conflict-key",
            command_type="catalog-revision",
            payload={"sku": "SKU-2"},
        )
    assert _count(db, WorkflowRun) == 1


def test_same_key_in_different_scope_is_independent(db) -> None:
    payload = {"sku": "SKU-1"}
    first = dispatch_command(
        db,
        scope="catalog-a",
        key="shared-key",
        command_type="catalog-revision",
        payload=payload,
    )
    second = dispatch_command(
        db,
        scope="catalog-b",
        key="shared-key",
        command_type="catalog-revision",
        payload=payload,
    )
    assert first["replayed"] is False
    assert second["replayed"] is False
    assert first["workflowId"] != second["workflowId"]
    assert _count(db, WorkflowRun) == 2
    assert _count(db, CatalogRevision) == 2


def test_dispatch_without_scope_or_key_raises(db) -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        dispatch_command(
            db,
            scope="",
            key="k",
            command_type="catalog-revision",
            payload={"sku": "SKU-1"},
        )
    with pytest.raises(ValidationError):
        dispatch_command(
            db,
            scope="catalog",
            key="",
            command_type="catalog-revision",
            payload={"sku": "SKU-1"},
        )


def test_unknown_command_type_raises(db) -> None:
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        dispatch_command(
            db,
            scope="catalog",
            key=f"key-{uuid.uuid4()}",
            command_type="no-such-command",
            payload={"sku": "SKU-1"},
        )
