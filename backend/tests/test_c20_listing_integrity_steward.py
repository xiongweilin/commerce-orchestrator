from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ValidationError
from app.experiments.listing_integrity_steward import (
    LISTING_INTEGRITY_MISSION,
    EscalationRoute,
    IntegrityAssessmentKind,
    MissionStatus,
    ShopifyReadback,
    StewardResourceEnvelope,
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
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now),
    )
    assessment = assess_listing_integrity(snapshot, assessment_id="assessment:c20:healthy")

    assert assessment.kind is IntegrityAssessmentKind.HEALTH_VERIFIED
    assert assessment.authority_bearing is False
    assert propose_listing_integrity_work(assessment, proposal_id="proposal:c20:none") is None


def test_drift_produces_diagnosis_proposal_not_external_effect_authority(db):
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(snapshot, assessment_id="assessment:c20:drift")
    proposal = propose_listing_integrity_work(assessment, proposal_id="proposal:c20:drift")

    assert assessment.kind is IntegrityAssessmentKind.DRIFT_DETECTED
    assert assessment.differences["title"] == ("C20 title", "drifted title")
    assert proposal is not None
    assert proposal.work_kind is StewardWorkKind.READ_ONLY_DIAGNOSIS
    assert proposal.authority_bearing is False
    assert route_external_repair() is EscalationRoute.HUMAN_DECISION_REQUIRED


def test_stale_qualification_yields_requalification_preparation_not_qualification(db):
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
    assessment = assess_listing_integrity(snapshot, assessment_id="assessment:c20:stale")
    proposal = propose_listing_integrity_work(assessment, proposal_id="proposal:c20:requalify")

    assert snapshot.qualification_current is False
    assert assessment.kind is IntegrityAssessmentKind.QUALIFICATION_NOT_CURRENT
    assert proposal is not None
    assert proposal.work_kind is StewardWorkKind.REQUALIFICATION_PREPARATION
    assert proposal.authority_bearing is False


def test_no_observed_failure_is_not_verified_health(db):
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
        before_due,
        assessment_id="assessment:c20:no-evidence",
    )
    assert before_assessment.kind is IntegrityAssessmentKind.INSUFFICIENT_EVIDENCE
    assert (
        propose_listing_integrity_work(
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
        after_due,
        assessment_id="assessment:c20:missing",
    )
    proposal = propose_listing_integrity_work(
        after_assessment,
        proposal_id="proposal:c20:readback",
    )
    assert after_assessment.kind is IntegrityAssessmentKind.EXPECTED_READBACK_MISSING
    assert proposal is not None
    assert proposal.work_kind is StewardWorkKind.READBACK_INVESTIGATION


def test_commitment_is_resource_bounded_and_does_not_mint_execution_authorization(db):
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(snapshot, assessment_id="assessment:c20:budget")
    proposal = propose_listing_integrity_work(assessment, proposal_id="proposal:c20:budget")
    assert proposal is not None

    with pytest.raises(ValidationError, match="resource envelope"):
        commit_steward_work(
            proposal,
            commitment_id="commitment:c20:too-small",
            envelope=StewardResourceEnvelope(
                api_calls=1,
                compute_units=1,
                human_attention_units=0,
            ),
            committed_at=now,
        )

    commitment = commit_steward_work(
        proposal,
        commitment_id="commitment:c20:bounded",
        envelope=StewardResourceEnvelope(
            api_calls=10,
            compute_units=10,
            human_attention_units=1,
        ),
        committed_at=now,
    )
    assert commitment.authority_bearing is False
    assert commitment.execution_authorization_ref is None


def test_completing_steward_work_does_not_discharge_listing_integrity_mission(db):
    _revision, listing = _listing(db)
    now = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
    snapshot = build_listing_integrity_snapshot(
        db,
        listing_id=listing.id,
        observed_at=now,
        readback=_readback(now, title="drifted title"),
    )
    assessment = assess_listing_integrity(snapshot, assessment_id="assessment:c20:complete")
    proposal = propose_listing_integrity_work(assessment, proposal_id="proposal:c20:complete")
    assert proposal is not None
    commitment = commit_steward_work(
        proposal,
        commitment_id="commitment:c20:complete",
        envelope=StewardResourceEnvelope(
            api_calls=10,
            compute_units=10,
            human_attention_units=1,
        ),
        committed_at=now,
    )
    completed_ref, mission_status = complete_steward_work(commitment)

    assert completed_ref == commitment.id
    assert mission_status is MissionStatus.ACTIVE
    assert LISTING_INTEGRITY_MISSION.status is MissionStatus.ACTIVE
