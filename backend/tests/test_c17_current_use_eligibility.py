from __future__ import annotations

from portable_runtime.public_contracts.models import (
    ExperienceUseAdmissionV1,
    ExperienceUseRequirementV1,
)

from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.responsibility import ResponsibilityObligationStatus
from app.services.current_use_eligibility import compose_current_use_eligibility
from app.services.realization_resolution import append_reconciliation_resolution
from app.services.responsibility import (
    discharge_responsibility_obligation,
    open_responsibility_obligation,
)


def _allowed_admission() -> ExperienceUseAdmissionV1:
    return ExperienceUseAdmissionV1(
        status="allowed",
        requirement_digest="a" * 64,
        snapshot_digest="b" * 64,
        resolved_snapshot={"schema": "test-c17"},
        reasons=[],
    )


def _requirement(*, channel: str = "shopify") -> ExperienceUseRequirementV1:
    return ExperienceUseRequirementV1(
        projection_refs=["projection:c17"],
        use_scope={"purpose": "publish", "channel": channel},
        subject_version_refs=["listing:c17:v1"],
        environment_bindings={"commerce": "prod-c17"},
        use_context={"workflow": "listing-publication"},
    )


def _reconciliation_resolution(db, actor):
    run = ReconciliationRun(
        run_type="c17-current-use",
        status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS,
    )
    db.add(run)
    db.flush()
    diff = ReconciliationDiff(
        run_id=run.id,
        domain="listing",
        entity_type="listing_publication",
        entity_id="listing:c17",
        expected={"content_hash": "expected"},
        actual={"content_hash": "changed"},
        difference={"field": "content_hash"},
        status=ReconciliationDiffStatus.OPEN,
    )
    db.add(diff)
    db.flush()
    return append_reconciliation_resolution(
        db,
        reconciliation_diff_id=diff.id,
        resolution_kind="accepted_difference",
        verification_status="verified",
        recorded_by_user_id=actor,
        basis_refs=[f"reconciliation_diff:{diff.id}"],
        evidence_refs=["shopify-readback:c17"],
    )


def test_matching_reconciliation_obligation_blocks_current_use_until_discharged(
    db, make_user
):
    actor = make_user(["system_admin"])
    resolution = _reconciliation_resolution(db, actor)
    obligation = open_responsibility_obligation(
        db,
        subject_ref="listing:c17",
        source_kind="reconciliation_resolution",
        source_ref=str(resolution.id),
        reason="current Shopify publication requires revalidation",
        scope={"channel": "shopify"},
        projection_refs=["projection:c17"],
        recorded_by_user_id=actor,
    )

    blocked = compose_current_use_eligibility(
        db,
        admission=_allowed_admission(),
        requirement=_requirement(),
    )
    assert blocked.portable_status == "allowed"
    assert blocked.status == "blocked"
    assert blocked.eligible is False
    assert blocked.authority_bearing is False
    assert blocked.applicable_obligation_refs == (str(obligation.id),)
    assert f"open-responsibility-obligation:{obligation.id}" in blocked.reasons

    discharge_responsibility_obligation(
        db,
        obligation_id=obligation.id,
        actor_user_id=actor,
    )
    restored = compose_current_use_eligibility(
        db,
        admission=_allowed_admission(),
        requirement=_requirement(),
    )
    db.refresh(obligation)
    assert obligation.status == ResponsibilityObligationStatus.DISCHARGED
    assert obligation.source_ref == str(resolution.id)
    assert restored.status == "allowed"
    assert restored.eligible is True
    assert restored.applicable_obligation_refs == ()


def test_obligation_is_scoped_not_globalized_across_channel_or_projection(db, make_user):
    actor = make_user(["system_admin"])
    resolution = _reconciliation_resolution(db, actor)
    open_responsibility_obligation(
        db,
        subject_ref="listing:c17",
        source_kind="reconciliation_resolution",
        source_ref=str(resolution.id),
        reason="Shopify-only revalidation",
        scope={"channel": "shopify"},
        projection_refs=["projection:c17"],
        recorded_by_user_id=actor,
    )
    open_responsibility_obligation(
        db,
        subject_ref="listing:other",
        source_kind="reconciliation_resolution",
        source_ref=f"{resolution.id}:other",
        reason="different projection",
        scope={"channel": "amazon"},
        projection_refs=["projection:other"],
        recorded_by_user_id=actor,
    )

    amazon = compose_current_use_eligibility(
        db,
        admission=_allowed_admission(),
        requirement=_requirement(channel="amazon"),
    )
    unrelated = compose_current_use_eligibility(
        db,
        admission=_allowed_admission(),
        requirement=ExperienceUseRequirementV1(
            projection_refs=["projection:unrelated"],
            use_scope={"purpose": "publish", "channel": "shopify"},
        ),
    )
    assert amazon.eligible is True
    assert amazon.applicable_obligation_refs == ()
    assert unrelated.eligible is True
    assert unrelated.applicable_obligation_refs == ()


def test_portable_negative_status_remains_decisive_without_obligations(db):
    admission = ExperienceUseAdmissionV1(
        status="stale",
        requirement_digest="c" * 64,
        snapshot_digest="d" * 64,
        resolved_snapshot={"schema": "test-c17"},
        reasons=["environment-drift:projection:c17"],
    )
    result = compose_current_use_eligibility(
        db,
        admission=admission,
        requirement=_requirement(),
    )
    assert result.eligible is False
    assert result.status == "stale"
    assert result.portable_status == "stale"
    assert result.reasons == ("environment-drift:projection:c17",)
