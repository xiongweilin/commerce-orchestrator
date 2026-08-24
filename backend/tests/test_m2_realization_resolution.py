from __future__ import annotations

import uuid

import pytest

from app.core.errors import ValidationError
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.effect_realization import (
    EffectRealizationAssessment,
    EffectRealizationStatus,
)
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.reconciliation_resolution import (
    ReconciliationResolution,
    ReconciliationResolutionKind,
    ReconciliationVerificationStatus,
)
from app.services.realization_resolution import (
    append_effect_realization_assessment,
    append_reconciliation_resolution,
    latest_effect_realization_assessment,
    latest_reconciliation_resolution,
)


def _effect(db, status: EffectStatus) -> EffectLedgerEntry:
    effect = EffectLedgerEntry(
        intent_id=uuid.uuid4(),
        target_system="shopify",
        operation="product_publish",
        status=status,
    )
    db.add(effect)
    db.flush()
    return effect


def _diff(db, status: ReconciliationDiffStatus) -> ReconciliationDiff:
    run = ReconciliationRun(run_type="m2-test", status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS)
    db.add(run)
    db.flush()
    diff = ReconciliationDiff(
        run_id=run.id,
        domain="effect",
        entity_type="effect",
        entity_id=str(uuid.uuid4()),
        status=status,
    )
    db.add(diff)
    db.flush()
    return diff


def test_succeeded_effect_can_remain_unknown_or_unverified(db, make_user) -> None:
    actor = make_user(["system_admin"])
    effect = _effect(db, EffectStatus.SUCCEEDED)
    unknown = append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.UNKNOWN,
        assessed_by_user_id=actor,
        evidence_refs=["check:remote-timeout"],
    )
    unverified = append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.UNVERIFIED,
        assessed_by_user_id=actor,
    )
    assert unknown.realization_status is EffectRealizationStatus.UNKNOWN
    assert unverified.realization_status is EffectRealizationStatus.UNVERIFIED
    assert effect.status is EffectStatus.SUCCEEDED


def test_outcome_unknown_can_later_be_verified_append_only(db, make_user) -> None:
    actor = make_user(["system_admin"])
    effect = _effect(db, EffectStatus.OUTCOME_UNKNOWN)
    first = append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.UNKNOWN,
        assessed_by_user_id=actor,
        evidence_refs=["observation:first"],
    )
    second = append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status=EffectRealizationStatus.VERIFIED,
        assessed_by_user_id=actor,
        evidence_refs=["observation:readback"],
    )
    assert first.id != second.id
    assert first.realization_status is EffectRealizationStatus.UNKNOWN
    assert latest_effect_realization_assessment(db, effect.id).id == second.id
    assert effect.status is EffectStatus.OUTCOME_UNKNOWN


def test_strong_realization_judgment_requires_evidence(db, make_user) -> None:
    actor = make_user(["system_admin"])
    effect = _effect(db, EffectStatus.SUCCEEDED)
    with pytest.raises(ValidationError, match="requires evidence_refs"):
        append_effect_realization_assessment(
            db,
            effect_id=effect.id,
            realization_status=EffectRealizationStatus.VERIFIED,
            assessed_by_user_id=actor,
        )


def test_historical_resolved_diff_has_no_synthetic_resolution(db) -> None:
    diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
    assert latest_reconciliation_resolution(db, diff.id) is None


def test_resolution_write_does_not_mutate_diff_lifecycle(db, make_user) -> None:
    actor = make_user(["accountant"])
    diff = _diff(db, ReconciliationDiffStatus.MANUAL_RECONCILIATION)
    resolution = append_reconciliation_resolution(
        db,
        reconciliation_diff_id=diff.id,
        resolution_kind=ReconciliationResolutionKind.REPAIRED,
        verification_status=ReconciliationVerificationStatus.UNVERIFIED,
        recorded_by_user_id=actor,
        basis_refs=["basis:operator-action"],
    )
    assert resolution.verification_status is ReconciliationVerificationStatus.UNVERIFIED
    assert diff.status is ReconciliationDiffStatus.MANUAL_RECONCILIATION


def test_disposition_and_verification_evolve_by_append_not_update(db, make_user) -> None:
    actor = make_user(["accountant"])
    diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
    first = append_reconciliation_resolution(
        db,
        reconciliation_diff_id=diff.id,
        resolution_kind=ReconciliationResolutionKind.ACCEPTED_DIFFERENCE,
        verification_status=ReconciliationVerificationStatus.UNVERIFIED,
        recorded_by_user_id=actor,
        basis_refs=["basis:policy-exception"],
    )
    second = append_reconciliation_resolution(
        db,
        reconciliation_diff_id=diff.id,
        resolution_kind=ReconciliationResolutionKind.ACCEPTED_DIFFERENCE,
        verification_status=ReconciliationVerificationStatus.VERIFIED,
        recorded_by_user_id=actor,
        basis_refs=["basis:policy-exception"],
        evidence_refs=["verification:independent-check"],
    )
    assert first.id != second.id
    assert first.verification_status is ReconciliationVerificationStatus.UNVERIFIED
    assert latest_reconciliation_resolution(db, diff.id).id == second.id
    assert diff.status is ReconciliationDiffStatus.RESOLVED


def test_api_verified_realization_retains_assessor_identity(
    client, db, make_user, auth_headers
) -> None:
    actor = make_user(["system_admin"])
    effect = _effect(db, EffectStatus.SUCCEEDED)
    db.commit()
    response = client.post(
        "/v1/effect-realizations",
        json={
            "effect_id": str(effect.id),
            "realization_status": "verified",
            "evidence_refs": ["readback:product"],
        },
        headers=auth_headers(actor, ["system_admin"]),
    )
    assert response.status_code == 200, response.text
    row = db.get(EffectRealizationAssessment, uuid.UUID(response.json()["assessmentId"]))
    assert row.assessed_by_user_id == actor
    assert response.json()["assessedByUserId"] == str(actor)


def test_api_reconciliation_resolution_retains_recorder_identity(
    client, db, make_user, auth_headers
) -> None:
    actor = make_user(["accountant"])
    diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
    db.commit()
    response = client.post(
        "/v1/reconciliation-resolutions",
        json={
            "reconciliation_diff_id": str(diff.id),
            "resolution_kind": "accepted_difference",
            "verification_status": "verified",
            "basis_refs": ["basis:policy-exception"],
            "evidence_refs": ["verification:review"],
        },
        headers=auth_headers(actor, ["accountant"]),
    )
    assert response.status_code == 200, response.text
    row = db.get(ReconciliationResolution, uuid.UUID(response.json()["resolutionId"]))
    assert row.recorded_by_user_id == actor
    assert response.json()["recordedByUserId"] == str(actor)
