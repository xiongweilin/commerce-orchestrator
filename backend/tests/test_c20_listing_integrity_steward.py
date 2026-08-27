from datetime import datetime, timedelta, timezone

import pytest
from portable_runtime.responsibility import (
    EffectClass,
    ResourceVector,
    ResponsibilityKernel,
    ResponsibilityStatus,
)
from portable_runtime.stores.memory import InMemoryStateStore

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
        envelope=ResourceVector(
            api_calls=10,
            compute_units=10,
            human_attention_units=1,
            concurrency_slots=2,
        ),
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
        envelope=ResourceVector(
            api_calls=10,
            compute_units=10,
            human_attention_units=1,
            concurrency_slots=2,
        ),
        committed_at=now,
    )
    completed_ref, mission_status = complete_steward_work(kernel, commitment)

    completed_work = kernel.store.get_work(completed_ref)
    assert completed_work is not None
    assert completed_work.status == "completed"
    assert mission_status is ResponsibilityStatus.ACTIVE
    assert kernel.current_status(LISTING_INTEGRITY_MISSION.id) is ResponsibilityStatus.ACTIVE
    assert kernel.store.list_authorizations() == []


def test_portable_mission_identity_is_not_a_model_session_or_authority() -> None:
    assert LISTING_INTEGRITY_MISSION.object_type == "StandingResponsibility"
    assert not hasattr(LISTING_INTEGRITY_MISSION, "provider_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "model_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "session_id")
    assert not hasattr(LISTING_INTEGRITY_MISSION, "execution_authorization")
    assert IntegrityAssessmentKind.HEALTH_VERIFIED.value == "health-verified"
