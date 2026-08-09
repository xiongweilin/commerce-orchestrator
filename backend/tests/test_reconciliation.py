"""Reconciliation: drift detection, escalation, manual-only resolution."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRunStatus,
)
from app.services.effect_ledger import record_effect
from app.services.reconciliation import (
    mark_diff_manual_reconciliation,
    resolve_diff,
    run_reconciliation,
)


def test_run_without_connector_skips_domain(db) -> None:
    run = run_reconciliation(db, run_type="daily", domains=["effect"])
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["by_domain"]["effect"]["status"] == "skipped"
    assert run.summary["auto_resolved"] == 0


def test_drift_creates_manual_reconciliation_diff(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    intent_id = str(entry.intent_id)

    def fake_connector(_domain: str) -> list[dict]:
        return [
            {
                "entity_type": "effect",
                "entity_id": intent_id,
                "state": "succeeded",  # remote says done, local says planned
            }
        ]

    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={"effect": fake_connector},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED_WITH_DIFFS
    assert run.summary["checked"] == 1
    assert run.summary["diffs"] == 1
    assert run.summary["auto_resolved"] == 0

    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert diff.domain == "effect"
    assert diff.entity_id == intent_id
    assert diff.difference == {"expected_state": "planned", "actual_state": "succeeded"}
    # Diffs are OPEN then immediately escalated; never auto-resolved.
    assert diff.status == ReconciliationDiffStatus.MANUAL_RECONCILIATION


def test_no_drift_when_states_agree(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    intent_id = str(entry.intent_id)

    def fake_connector(_domain: str) -> list[dict]:
        return [
            {
                "entity_type": "effect",
                "entity_id": intent_id,
                "state": "planned",
            }
        ]

    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={"effect": fake_connector},
    )
    assert run.status == ReconciliationRunStatus.COMPLETED
    assert run.summary["diffs"] == 0
    assert _diff_count(db) == 0


def _diff_count(db) -> int:
    return len(db.execute(select(ReconciliationDiff)).scalars().all())


def test_missing_remote_row_is_a_diff(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    intent_id = str(entry.intent_id)
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={"effect": lambda _d: []},
    )
    assert run.summary["diffs"] == 1
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert diff.difference.get("missing_in_remote") is True
    assert diff.entity_id == intent_id


def test_resolve_diff_requires_note_and_manual_status(db) -> None:
    entry = record_effect(db, target_system="shopify", operation="product_publish")
    run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={
            "effect": lambda _d: [
                {
                    "entity_type": "effect",
                    "entity_id": str(entry.intent_id),
                    "state": "succeeded",
                }
            ]
        },
    )
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    assert diff.status == ReconciliationDiffStatus.MANUAL_RECONCILIATION

    resolved = resolve_diff(db, diff_id=diff.id, note="manual fix applied")
    assert resolved.status == ReconciliationDiffStatus.RESOLVED
    assert resolved.resolution_note == "manual fix applied"

    with pytest.raises(ConflictError, match="MANUAL_RECONCILIATION"):
        resolve_diff(db, diff_id=diff.id, note="again")


def test_resolve_without_note_rejected(db) -> None:
    run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={
            "effect": lambda _d: [
                {"entity_type": "effect", "entity_id": "zzz", "state": "succeeded"}
            ]
        },
    )
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    with pytest.raises(ValidationError):
        resolve_diff(db, diff_id=diff.id, note="   ")


def test_cannot_resolve_open_diff(db) -> None:
    run = run_reconciliation(db, run_type="daily", domains=["effect"])
    open_diff = ReconciliationDiff(
        run_id=run.id,
        domain="effect",
        entity_type="effect",
        entity_id="x",
        expected={"status": "planned"},
        actual={"status": "succeeded"},
        difference={"expected_state": "planned", "actual_state": "succeeded"},
        status=ReconciliationDiffStatus.OPEN,
    )
    db.add(open_diff)
    db.flush()
    with pytest.raises(ConflictError, match="MANUAL_RECONCILIATION"):
        resolve_diff(db, diff_id=open_diff.id, note="auto-smoothing forbidden")


def test_mark_diff_manual_reconciliation_escalates_open(db) -> None:
    run = run_reconciliation(db, run_type="daily", domains=["effect"])
    open_diff = ReconciliationDiff(
        run_id=run.id,
        domain="effect",
        entity_type="effect",
        entity_id="x",
        expected={},
        actual={},
        difference={},
        status=ReconciliationDiffStatus.OPEN,
    )
    db.add(open_diff)
    db.flush()
    escalated = mark_diff_manual_reconciliation(db, open_diff.id)
    assert escalated.status == ReconciliationDiffStatus.MANUAL_RECONCILIATION
    with pytest.raises(ConflictError):
        mark_diff_manual_reconciliation(db, open_diff.id)


def test_run_validation(db) -> None:
    with pytest.raises(ValidationError):
        run_reconciliation(db, run_type="", domains=["effect"])
    with pytest.raises(ValidationError):
        run_reconciliation(db, run_type="daily", domains=[])
    with pytest.raises(ValidationError):
        run_reconciliation(db, run_type="daily", domains=["no-such-domain"])
    with pytest.raises(NotFoundError):
        resolve_diff(db, diff_id=uuid.uuid4(), note="nope")
