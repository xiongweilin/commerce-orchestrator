from datetime import datetime, timedelta, timezone

import pytest
from portable_runtime.core.models import Run
from portable_runtime.records.models import EvidenceArtifact
from portable_runtime.responsibility import (
    EffectClass,
    ResourceVector,
    ResponsibilityKernel,
    ResponsibilityStatus,
)
from portable_runtime.stores.memory import InMemoryStateStore
from portable_runtime.stores.sqlite import SQLiteStateStore
from portable_runtime.workflows.completion import CompletionAuthority

from app.core.errors import ValidationError
from app.experiments.listing_integrity_steward import (
    LISTING_INTEGRITY_MISSION,
    EscalationRoute,
    IntegrityAssessmentKind,
    ShopifyReadback,
    StewardWorkKind,
    assess_listing_integrity,
    build_listing_integrity_snapshot,
    commit_steward_work,
    complete_steward_work,
    propose_listing_integrity_work,
    route_external_repair,
)
from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.listing import ListingPublication, ListingStatus
from app.models.publication_qualification import PublicationQualificationStatus
from app.services.publication_qualification import append_publication_qualification_assessment


def _kernel() -> ResponsibilityKernel:
    return ResponsibilityKernel(InMemoryStateStore())


def _listing(db):
    revision = CatalogRevision(
        sku="SKU-C20",
        title="C20 title",
        description="C20 description",
        category="widgets",
        status=CatalogRevisionStatus.APPROVED,
        proposed={"title": "C20 title"},
    )
    db.add(revision)
    db.flush()
    context = {
        "purpose": "publish",
        "policy_version": "policy-c20",
        "adapter_version": "shopify-c20",
        "environment_ref": "prod-c20",
    }
    append_publication_qualification_assessment(
        db,
        catalog_revision_id=revision.id,
        channel="shopify",
        purpose=context["purpose"],
        policy_version=context["policy_version"],
        adapter_version=context["adapter_version"],
        environment_ref=context["environment_ref"],
        assessment_status=PublicationQualificationStatus.QUALIFIED,
        evidence_refs=["qualification:c20"],
    )
    listing = ListingPublication(
        sku=revision.sku,
        channel="shopify",
        status=ListingStatus.ACTIVE,
        payload={
            "catalog_revision_id": str(revision.id),
            "qualification_context": context,
        },
        shopify_product_gid="gid://shopify/Product/C20",
    )
    db.add(listing)
    db.flush()
    return revision, listing


def _readback(now: datetime, *, title: str = "C20 title") -> ShopifyReadback:
    return ShopifyReadback(
        sku="SKU-C20",
        title=title,
        description="C20 description",
        category="widgets",
        observed_at=now,
        evidence_ref=f"shopify-readback:{now.isoformat()}",
        source_version_ref="shopify-product:C20:v7",
    )


def _verification_proof(work, run: Run, *, proof_id: str) -> EvidenceArtifact:
    return EvidenceArtifact(
        id=proof_id,
        kind="closed-verification",
        lifecycle_status="current",
        metadata={
            "verification_result": {"result": "pass"},
            "work_id": work.id,
            "run_id": run.id,
            "verification_scope": {},
            "work_version": 1,
            "acceptance_criteria": list(work.acceptance_criteria),
            "obligation_refs": CompletionAuthority.required_obligation_refs(work),
        },
    )


def _large_envelope() -> ResourceVector:
    return ResourceVector(
        api_calls=10,
        compute_units=10,
        human_attention_units=1,
        concurrency_slots=2,
    )


def test_current_readback_can_verify_health_without_creating_work(db):
    kernel = _kernel()
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now),
    )
    assessment = assess_listing_integrity(
        kernel,
        snapshot,
        assessment_id="assessment:c20:healthy",
    )

    assert assessment.assessment_kind == "listing-integrity:health-verified"
    assert (
        propose_listing_integrity_work(
            kernel,
            assessment,
            proposal_id="proposal:c20:none",
        )
        is None
    )
    assert kernel.store.list_work() == []
    assert kernel.store.list_authorizations() == []
    assert kernel.current_status(LISTING_INTEGRITY_MISSION.id) is ResponsibilityStatus.ACTIVE


def test_drift_produces_diagnosis_proposal_not_external_effect_authority(db):
    kernel = _kernel()
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(
        kernel,
        snapshot,
        assessment_id="assessment:c20:drift",
    )
    proposal = propose_listing_integrity_work(
        kernel,
        assessment,
        proposal_id="proposal:c20:drift",
    )

    assert assessment.assessment_kind == "listing-integrity:drift-detected"
    assert snapshot.expected_fields["title"] == "C20 title"
    assert snapshot.observed_fields is not None
    assert snapshot.observed_fields["title"] == "drifted title"
    assert proposal is not None
    assert proposal.work_kind == StewardWorkKind.READ_ONLY_DIAGNOSIS.value
    assert proposal.effect_class is EffectClass.READ_ONLY
    assert kernel.store.list_work() == []
    assert kernel.store.list_authorizations() == []
    assert route_external_repair() is EscalationRoute.HUMAN_DECISION_REQUIRED


def test_stale_qualification_yields_requalification_preparation_not_qualification(db):
    kernel = _kernel()
    revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)

    revision.title = "C20 title v2"
    db.flush()

    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=ShopifyReadback(
            sku="SKU-C20",
            title="C20 title v2",
            description="C20 description",
            category="widgets",
            observed_at=now,
            evidence_ref="shopify-readback:stale-qualification",
        ),
    )
    assessment = assess_listing_integrity(
        kernel,
        snapshot,
        assessment_id="assessment:c20:stale",
    )
    proposal = propose_listing_integrity_work(
        kernel,
        assessment,
        proposal_id="proposal:c20:requalify",
    )

    assert snapshot.qualification_current is False
    assert assessment.assessment_kind == "listing-integrity:qualification-not-current"
    assert proposal is not None
    assert proposal.work_kind == StewardWorkKind.REQUALIFICATION_PREPARATION.value
    assert proposal.effect_class is EffectClass.READ_ONLY
    assert kernel.store.list_authorizations() == []


def test_no_observed_failure_is_not_verified_health(db):
    kernel = _kernel()
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)

    before_due = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=None,
        readback_expected_by=now + timedelta(minutes=5),
    )
    before_assessment = assess_listing_integrity(
        kernel,
        before_due,
        assessment_id="assessment:c20:no-evidence",
    )
    assert before_assessment.assessment_kind == "listing-integrity:insufficient-evidence"
    assert (
        propose_listing_integrity_work(
            kernel,
            before_assessment,
            proposal_id="proposal:c20:no-evidence",
        )
        is None
    )

    after_due = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now + timedelta(minutes=10),
        readback=None,
        readback_expected_by=now + timedelta(minutes=5),
    )
    after_assessment = assess_listing_integrity(
        kernel,
        after_due,
        assessment_id="assessment:c20:missing",
    )
    proposal = propose_listing_integrity_work(
        kernel,
        after_assessment,
        proposal_id="proposal:c20:readback",
    )
    assert after_assessment.assessment_kind == "listing-integrity:expected-readback-missing"
    assert proposal is not None
    assert proposal.work_kind == StewardWorkKind.READ_ONLY_DIAGNOSIS.value
    assert proposal.effect_class is EffectClass.READ_ONLY


def test_commitment_is_resource_bounded_and_does_not_mint_execution_authorization(db):
    kernel = _kernel()
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(
        kernel,
        snapshot,
        assessment_id="assessment:c20:budget",
    )
    proposal = propose_listing_integrity_work(
        kernel,
        assessment,
        proposal_id="proposal:c20:budget",
    )
    assert proposal is not None

    with pytest.raises(ValidationError, match="resource envelope"):
        commit_steward_work(
            kernel,
            proposal,
            commitment_id="commitment:c20:too-small",
            envelope=ResourceVector(
                api_calls=1,
                compute_units=1,
                human_attention_units=0,
                concurrency_slots=0,
            ),
            committed_at=now,
        )

    commitment = commit_steward_work(
        kernel,
        proposal,
        commitment_id="commitment:c20:bounded",
        envelope=_large_envelope(),
        committed_at=now,
    )
    assert commitment.object_type == "Commitment"
    assert kernel.store.list_authorizations() == []


def test_completing_steward_work_does_not_discharge_listing_integrity_mission(db):
    kernel = _kernel()
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(
        kernel,
        snapshot,
        assessment_id="assessment:c20:complete",
    )
    proposal = propose_listing_integrity_work(
        kernel,
        assessment,
        proposal_id="proposal:c20:complete",
    )
    assert proposal is not None
    commitment = commit_steward_work(
        kernel,
        proposal,
        commitment_id="commitment:c20:complete",
        envelope=_large_envelope(),
        committed_at=now,
    )
    work = kernel.materialize_work(commitment.id)
    run = Run(id="run:c20:complete", work_id=work.id, status="running")
    kernel.store.save_run(run)
    proof = _verification_proof(work, run, proof_id="proof:c20:complete")
    kernel.store.save_record(proof)

    completed_ref, mission_status = complete_steward_work(
        kernel,
        commitment,
        run=run,
        verification_refs=[proof.id],
    )

    completed_work = kernel.store.get_work(completed_ref)
    assert completed_work is not None
    assert completed_work.status == "completed"
    assert mission_status is ResponsibilityStatus.ACTIVE
    assert kernel.current_status(LISTING_INTEGRITY_MISSION.id) is ResponsibilityStatus.ACTIVE
    assert kernel.store.list_authorizations() == []


def test_sqlite_restart_preserves_responsibility_history_without_minting_authority(db, tmp_path):
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    state_path = tmp_path / "commerce-responsibility.db"

    store_a = SQLiteStateStore(state_path)
    kernel_a = ResponsibilityKernel(store_a)
    assessment = assess_listing_integrity(
        kernel_a,
        snapshot,
        assessment_id="assessment:c20:restart",
    )
    assert kernel_a.current_status(LISTING_INTEGRITY_MISSION.id) is ResponsibilityStatus.ACTIVE
    store_a.close()

    store_b = SQLiteStateStore(state_path)
    kernel_b = ResponsibilityKernel(store_b)
    try:
        identity = kernel_b.get_responsibility(LISTING_INTEGRITY_MISSION.id)
        assert identity.id == LISTING_INTEGRITY_MISSION.id
        assert kernel_b.current_definition(identity.id)[0] == 1
        assert kernel_b.current_status(identity.id) is ResponsibilityStatus.ACTIVE
        recovered_assessment = kernel_b.journal.get(assessment.id)
        assert recovered_assessment is not None

        proposal = propose_listing_integrity_work(
            kernel_b,
            recovered_assessment,
            proposal_id="proposal:c20:restart",
        )
        assert proposal is not None
        commitment = commit_steward_work(
            kernel_b,
            proposal,
            commitment_id="commitment:c20:restart",
            envelope=_large_envelope(),
            committed_at=now,
        )
        work = kernel_b.materialize_work(commitment.id)
        run = Run(id="run:c20:restart", work_id=work.id, status="running")
        kernel_b.store.save_run(run)
        proof = _verification_proof(work, run, proof_id="proof:c20:restart")
        kernel_b.store.save_record(proof)
        complete_steward_work(
            kernel_b,
            commitment,
            run=run,
            verification_refs=[proof.id],
        )
        assert kernel_b.store.list_authorizations() == []
    finally:
        store_b.close()

    store_c = SQLiteStateStore(state_path)
    kernel_c = ResponsibilityKernel(store_c)
    try:
        assert kernel_c.current_status(LISTING_INTEGRITY_MISSION.id) is ResponsibilityStatus.ACTIVE
        assert kernel_c.current_definition(LISTING_INTEGRITY_MISSION.id)[0] == 1
        assert kernel_c.journal.get("proposal:c20:restart") is not None
        assert kernel_c.journal.get("commitment:c20:restart") is not None
        works = kernel_c.store.list_work()
        assert len(works) == 1
        assert works[0].status == "completed"
        assert kernel_c.store.list_authorizations() == []
    finally:
        store_c.close()


def test_portable_mission_identity_is_not_a_model_session_or_authority() -> None:
    assert LISTING_INTEGRITY_MISSION.object_type == "StandingResponsibility"
    assert not hasattr(LISTING_INTEGRITY_MISSION, "provider_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "model_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "session_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "execution_authorization")
    assert IntegrityAssessmentKind.HEALTH_VERIFIED.value == "health-verified"
