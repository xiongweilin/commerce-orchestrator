"""Experimental persistent Listing Integrity Steward.

The steward is a reference-domain specialization for Stage-4 persistent agency.
It can observe, assess, propose, and commit bounded diagnostic work. It cannot
approve publication changes, mint ExecutionAuthorization, or treat its own
assessment as Shopify/Odoo reality.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.core.errors import ValidationError
from app.models.catalog import CatalogRevision
from app.models.listing import ListingPublication
from app.models.publication_qualification import PublicationQualificationStatus
from app.services.publication_qualification import (
    catalog_revision_fingerprint,
    latest_applicable_assessment,
)


class MissionStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISCHARGED = "discharged"


class IntegrityAssessmentKind(StrEnum):
    HEALTH_VERIFIED = "health-verified"
    DRIFT_DETECTED = "drift-detected"
    QUALIFICATION_NOT_CURRENT = "qualification-not-current"
    EXPECTED_READBACK_MISSING = "expected-readback-missing"
    INSUFFICIENT_EVIDENCE = "insufficient-evidence"


class StewardWorkKind(StrEnum):
    READ_ONLY_DIAGNOSIS = "read-only-diagnosis"
    REQUALIFICATION_PREPARATION = "requalification-preparation"
    READBACK_INVESTIGATION = "readback-investigation"


class EscalationRoute(StrEnum):
    AUTONOMOUS_DIAGNOSIS = "autonomous-diagnosis"
    HUMAN_DECISION_REQUIRED = "human-decision-required"


@dataclass(frozen=True, slots=True)
class ListingIntegrityMission:
    id: str
    statement: str
    channel: str
    status: MissionStatus = MissionStatus.ACTIVE
    authority_bearing: bool = False


LISTING_INTEGRITY_MISSION = ListingIntegrityMission(
    id="mission:listing-integrity:shopify",
    statement="Maintain Shopify listing integrity against the currently qualified catalog state.",
    channel="shopify",
)


@dataclass(frozen=True, slots=True)
class ShopifyReadback:
    sku: str
    title: str
    description: str | None
    category: str | None
    observed_at: datetime
    evidence_ref: str
    source_version_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ListingIntegritySnapshot:
    listing_ref: str
    listing_version: int
    catalog_revision_ref: str
    catalog_fingerprint: str
    qualification_ref: str | None
    qualification_current: bool
    expected_fields: dict[str, object]
    observed_fields: dict[str, object] | None
    evidence_refs: tuple[str, ...]
    observed_at: datetime
    readback_expected_by: datetime | None = None


@dataclass(frozen=True, slots=True)
class ListingIntegrityAssessment:
    id: str
    mission_ref: str
    listing_ref: str
    kind: IntegrityAssessmentKind
    basis_refs: tuple[str, ...]
    assessed_at: datetime
    differences: dict[str, tuple[object, object]] = field(default_factory=dict)
    rationale: str = ""
    authority_bearing: bool = False


@dataclass(frozen=True, slots=True)
class StewardResourceRequest:
    api_calls: int
    compute_units: int
    human_attention_units: int = 0

    def __post_init__(self) -> None:
        if min(self.api_calls, self.compute_units, self.human_attention_units) < 0:
            raise ValueError("resource request cannot be negative")


@dataclass(frozen=True, slots=True)
class StewardResourceEnvelope:
    api_calls: int
    compute_units: int
    human_attention_units: int

    def admits(self, request: StewardResourceRequest) -> bool:
        return (
            request.api_calls <= self.api_calls
            and request.compute_units <= self.compute_units
            and request.human_attention_units <= self.human_attention_units
        )


@dataclass(frozen=True, slots=True)
class ListingIntegrityWorkProposal:
    id: str
    mission_ref: str
    assessment_ref: str
    listing_ref: str
    work_kind: StewardWorkKind
    resource_request: StewardResourceRequest
    stop_conditions: tuple[str, ...]
    escalation_conditions: tuple[str, ...]
    authority_bearing: bool = False


@dataclass(frozen=True, slots=True)
class ListingIntegrityCommitment:
    id: str
    mission_ref: str
    proposal_ref: str
    allocation: StewardResourceRequest
    committed_at: datetime
    authority_bearing: bool = False
    execution_authorization_ref: str | None = None


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"listing integrity requires {field_name}")
    return text


def build_listing_integrity_snapshot(
    db,
    *,
    listing_id: uuid.UUID,
    observed_at: datetime,
    readback: ShopifyReadback | None,
    readback_expected_by: datetime | None = None,
) -> ListingIntegritySnapshot:
    """Compose a projection from Commerce-owned and Shopify-owned facts.

    The caller supplies Shopify readback evidence. This function never treats
    Commerce memory as Shopify reality and never mutates either fact owner.
    """

    listing = db.get(ListingPublication, listing_id)
    if listing is None:
        raise ValidationError("listing integrity requires a known listing")
    if listing.channel != LISTING_INTEGRITY_MISSION.channel:
        raise ValidationError("Listing Integrity Steward only specializes Shopify listings")

    payload = listing.payload or {}
    raw_revision_id = payload.get("catalog_revision_id") or payload.get("revision_id")
    if raw_revision_id is None:
        raise ValidationError("listing integrity requires a catalog revision binding")
    try:
        revision_id = uuid.UUID(str(raw_revision_id))
    except ValueError as exc:
        raise ValidationError("listing catalog revision binding is invalid") from exc
    revision = db.get(CatalogRevision, revision_id)
    if revision is None:
        raise ValidationError("listing integrity catalog revision is missing")
    if revision.sku != listing.sku:
        raise ValidationError("listing and catalog revision SKU do not match")

    context = payload.get("qualification_context") or {}
    purpose = _required_text(context.get("purpose"), "qualification purpose")
    policy_version = _required_text(context.get("policy_version"), "qualification policy_version")
    adapter_version = _required_text(
        context.get("adapter_version"), "qualification adapter_version"
    )
    environment_ref = _required_text(
        context.get("environment_ref"), "qualification environment_ref"
    )
    qualification = latest_applicable_assessment(
        db,
        revision=revision,
        channel=listing.channel,
        purpose=purpose,
        policy_version=policy_version,
        adapter_version=adapter_version,
        environment_ref=environment_ref,
    )
    qualification_current = bool(
        qualification is not None
        and qualification.assessment_status is PublicationQualificationStatus.QUALIFIED
    )

    expected_fields = {
        "sku": revision.sku,
        "title": revision.title,
        "description": revision.description,
        "category": revision.category,
    }
    observed_fields = None
    evidence_refs: list[str] = [f"catalog_revision:{revision.id}"]
    if qualification is not None:
        evidence_refs.append(f"publication_qualification:{qualification.id}")
    if readback is not None:
        if readback.sku != listing.sku:
            raise ValidationError("Shopify readback SKU does not match listing")
        observed_fields = {
            "sku": readback.sku,
            "title": readback.title,
            "description": readback.description,
            "category": readback.category,
        }
        evidence_refs.append(_required_text(readback.evidence_ref, "Shopify readback evidence_ref"))
        if readback.source_version_ref:
            evidence_refs.append(readback.source_version_ref)

    return ListingIntegritySnapshot(
        listing_ref=f"listing:{listing.id}",
        listing_version=listing.version,
        catalog_revision_ref=f"catalog_revision:{revision.id}",
        catalog_fingerprint=catalog_revision_fingerprint(revision),
        qualification_ref=(
            None if qualification is None else f"publication_qualification:{qualification.id}"
        ),
        qualification_current=qualification_current,
        expected_fields=expected_fields,
        observed_fields=observed_fields,
        evidence_refs=tuple(evidence_refs),
        observed_at=observed_at,
        readback_expected_by=readback_expected_by,
    )


def assess_listing_integrity(
    snapshot: ListingIntegritySnapshot,
    *,
    assessment_id: str,
) -> ListingIntegrityAssessment:
    """Assess current integrity without granting any action authority."""

    if not snapshot.qualification_current:
        return ListingIntegrityAssessment(
            id=assessment_id,
            mission_ref=LISTING_INTEGRITY_MISSION.id,
            listing_ref=snapshot.listing_ref,
            kind=IntegrityAssessmentKind.QUALIFICATION_NOT_CURRENT,
            basis_refs=snapshot.evidence_refs,
            assessed_at=snapshot.observed_at,
            rationale="current-use publication qualification is absent or stale",
        )

    if snapshot.observed_fields is None:
        missing_is_due = (
            snapshot.readback_expected_by is not None
            and snapshot.observed_at >= snapshot.readback_expected_by
        )
        kind = (
            IntegrityAssessmentKind.EXPECTED_READBACK_MISSING
            if missing_is_due
            else IntegrityAssessmentKind.INSUFFICIENT_EVIDENCE
        )
        return ListingIntegrityAssessment(
            id=assessment_id,
            mission_ref=LISTING_INTEGRITY_MISSION.id,
            listing_ref=snapshot.listing_ref,
            kind=kind,
            basis_refs=snapshot.evidence_refs,
            assessed_at=snapshot.observed_at,
            rationale=(
                "expected Shopify readback evidence is missing"
                if missing_is_due
                else "no current Shopify readback exists, so health is not verified"
            ),
        )

    differences = {
        key: (expected, snapshot.observed_fields.get(key))
        for key, expected in snapshot.expected_fields.items()
        if snapshot.observed_fields.get(key) != expected
    }
    if differences:
        return ListingIntegrityAssessment(
            id=assessment_id,
            mission_ref=LISTING_INTEGRITY_MISSION.id,
            listing_ref=snapshot.listing_ref,
            kind=IntegrityAssessmentKind.DRIFT_DETECTED,
            basis_refs=snapshot.evidence_refs,
            assessed_at=snapshot.observed_at,
            differences=differences,
            rationale="Shopify readback differs from currently qualified catalog fields",
        )

    return ListingIntegrityAssessment(
        id=assessment_id,
        mission_ref=LISTING_INTEGRITY_MISSION.id,
        listing_ref=snapshot.listing_ref,
        kind=IntegrityAssessmentKind.HEALTH_VERIFIED,
        basis_refs=snapshot.evidence_refs,
        assessed_at=snapshot.observed_at,
        rationale="current Shopify readback matches currently qualified catalog fields",
    )


def propose_listing_integrity_work(
    assessment: ListingIntegrityAssessment,
    *,
    proposal_id: str,
) -> ListingIntegrityWorkProposal | None:
    """Generate non-authority-bearing work proposals from current assessments."""

    if assessment.kind is IntegrityAssessmentKind.HEALTH_VERIFIED:
        return None
    if assessment.kind is IntegrityAssessmentKind.INSUFFICIENT_EVIDENCE:
        return None

    if assessment.kind is IntegrityAssessmentKind.DRIFT_DETECTED:
        work_kind = StewardWorkKind.READ_ONLY_DIAGNOSIS
        request = StewardResourceRequest(api_calls=4, compute_units=2)
        stop = ("drift-cause-explained", "repair-proposal-prepared")
        escalation = ("external-listing-change-required",)
    elif assessment.kind is IntegrityAssessmentKind.QUALIFICATION_NOT_CURRENT:
        work_kind = StewardWorkKind.REQUALIFICATION_PREPARATION
        request = StewardResourceRequest(api_calls=1, compute_units=2, human_attention_units=1)
        stop = ("qualification-evidence-prepared",)
        escalation = ("publication-qualification-decision-required",)
    else:
        work_kind = StewardWorkKind.READBACK_INVESTIGATION
        request = StewardResourceRequest(api_calls=3, compute_units=1)
        stop = ("readback-obtained", "missing-signal-cause-explained")
        escalation = ("source-owner-investigation-required",)

    return ListingIntegrityWorkProposal(
        id=proposal_id,
        mission_ref=assessment.mission_ref,
        assessment_ref=assessment.id,
        listing_ref=assessment.listing_ref,
        work_kind=work_kind,
        resource_request=request,
        stop_conditions=stop,
        escalation_conditions=escalation,
    )


def commit_steward_work(
    proposal: ListingIntegrityWorkProposal,
    *,
    commitment_id: str,
    envelope: StewardResourceEnvelope,
    committed_at: datetime,
) -> ListingIntegrityCommitment:
    """Commit bounded steward resources without minting effect authority."""

    if not envelope.admits(proposal.resource_request):
        raise ValidationError("listing integrity proposal exceeds steward resource envelope")
    return ListingIntegrityCommitment(
        id=commitment_id,
        mission_ref=proposal.mission_ref,
        proposal_ref=proposal.id,
        allocation=proposal.resource_request,
        committed_at=committed_at,
    )


def route_external_repair() -> EscalationRoute:
    """External listing mutation stays on the existing human Decision/Authorization path."""

    return EscalationRoute.HUMAN_DECISION_REQUIRED


def complete_steward_work(
    commitment: ListingIntegrityCommitment,
) -> tuple[str, MissionStatus]:
    """Return bounded task completion while keeping the standing mission active."""

    return commitment.id, LISTING_INTEGRITY_MISSION.status
