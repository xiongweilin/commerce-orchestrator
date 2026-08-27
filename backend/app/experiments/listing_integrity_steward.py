"""Persistent Listing Integrity Steward over portable responsibility objects.

Commerce still owns catalog, publication-qualification, Shopify readback,
authorization, DBOS and effect/outcome facts. This module specializes the
portable persistent-responsibility contract for the listing-integrity mission;
it does not maintain a parallel Mission/Assessment/Proposal/Commitment model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from portable_runtime.core.models import Run
from portable_runtime.responsibility import (
    Commitment,
    ListingIntegrityState,
    PortfolioAdmissionDecision,
    PriorityDimensions,
    PriorityJudgment,
    ResourcePool,
    ResourceReservation,
    ResourceVector,
    ResponsibilityAdmission,
    ResponsibilityAssessment,
    ResponsibilityKernel,
    ResponsibilityStatus,
    StandingResponsibility,
    WorkProposal,
    listing_integrity_proposal,
    record_domain_assessment,
)
from portable_runtime.workflows.completion import CompletionAuthority

from app.core.errors import ValidationError
from app.models.catalog import CatalogRevision
from app.models.listing import ListingPublication
from app.models.publication_qualification import PublicationQualificationStatus
from app.services.publication_qualification import (
    catalog_revision_fingerprint,
    latest_applicable_assessment,
)

MissionStatus = ResponsibilityStatus
IntegrityAssessmentKind = ListingIntegrityState


class StewardWorkKind(StrEnum):
    READ_ONLY_DIAGNOSIS = "listing-integrity-diagnosis"
    REQUALIFICATION_PREPARATION = "listing-requalification-preparation"


class EscalationRoute(StrEnum):
    AUTONOMOUS_DIAGNOSIS = "autonomous-diagnosis"
    HUMAN_DECISION_REQUIRED = "human-decision-required"


LISTING_INTEGRITY_MISSION = StandingResponsibility(
    id="mission:listing-integrity:shopify",
    responsibility_kind="commerce-listing-integrity",
    statement="Maintain Shopify listing integrity against the currently qualified catalog state.",
    scope={"channel": "shopify"},
)

_PORTFOLIO_POLICY = "commerce-listing-steward-policy-v1"
_RESOURCE_POOL_ID = "resource_pool:commerce-listing-integrity"


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


def ensure_listing_integrity_responsibility(kernel: ResponsibilityKernel) -> StandingResponsibility:
    """Admit the durable mission once; admission itself grants no effect authority."""

    existing = kernel.journal.get(LISTING_INTEGRITY_MISSION.id)
    if existing is None:
        kernel.register(
            LISTING_INTEGRITY_MISSION,
            ResponsibilityAdmission(
                id="admission:listing-integrity:shopify",
                responsibility_ref=LISTING_INTEGRITY_MISSION.id,
                responsibility_version=1,
                principal_ref="commerce:operator",
                basis_refs=["experiment:c20-listing-integrity"],
            ),
        )
        return LISTING_INTEGRITY_MISSION
    if not isinstance(existing, StandingResponsibility):
        raise ValidationError("listing integrity mission id is bound to another object type")
    return existing


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
    """Compose a projection from Commerce-owned and Shopify-owned facts."""

    listing = db.get(ListingPublication, listing_id)
    if listing is None:
        raise ValidationError("listing integrity requires a known listing")
    if listing.channel != str(LISTING_INTEGRITY_MISSION.scope["channel"]):
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


def _classify(snapshot: ListingIntegritySnapshot) -> ListingIntegrityState:
    if not snapshot.qualification_current:
        return ListingIntegrityState.QUALIFICATION_NOT_CURRENT
    if snapshot.observed_fields is None:
        missing_is_due = (
            snapshot.readback_expected_by is not None
            and snapshot.observed_at >= snapshot.readback_expected_by
        )
        if missing_is_due:
            return ListingIntegrityState.EXPECTED_READBACK_MISSING
        return ListingIntegrityState.INSUFFICIENT_EVIDENCE
    differences = {
        key: (expected, snapshot.observed_fields.get(key))
        for key, expected in snapshot.expected_fields.items()
        if snapshot.observed_fields.get(key) != expected
    }
    if differences:
        return ListingIntegrityState.DRIFT_DETECTED
    return ListingIntegrityState.HEALTH_VERIFIED


def _rationale(state: ListingIntegrityState) -> str:
    return {
        ListingIntegrityState.HEALTH_VERIFIED: (
            "current Shopify readback matches currently qualified catalog fields"
        ),
        ListingIntegrityState.DRIFT_DETECTED: (
            "Shopify readback differs from currently qualified catalog fields"
        ),
        ListingIntegrityState.QUALIFICATION_NOT_CURRENT: (
            "current-use publication qualification is absent or stale"
        ),
        ListingIntegrityState.EXPECTED_READBACK_MISSING: (
            "expected Shopify readback evidence is missing"
        ),
        ListingIntegrityState.INSUFFICIENT_EVIDENCE: (
            "no current Shopify readback exists, so health is not verified"
        ),
    }[state]


def assess_listing_integrity(
    kernel: ResponsibilityKernel,
    snapshot: ListingIntegritySnapshot,
    *,
    assessment_id: str,
) -> ResponsibilityAssessment:
    """Record a portable assessment from Commerce-owned current facts."""

    ensure_listing_integrity_responsibility(kernel)
    version, _statement, _scope = kernel.current_definition(LISTING_INTEGRITY_MISSION.id)
    state = _classify(snapshot)
    assessment = ResponsibilityAssessment(
        id=assessment_id,
        responsibility_ref=LISTING_INTEGRITY_MISSION.id,
        responsibility_version=version,
        subject_ref=snapshot.listing_ref,
        assessment_kind=f"listing-integrity:{state.value}",
        basis_refs=list(snapshot.evidence_refs),
        assessed_at=snapshot.observed_at,
        rationale=_rationale(state),
    )
    return record_domain_assessment(kernel, assessment, now=snapshot.observed_at)


def _assessment_state(assessment: ResponsibilityAssessment) -> ListingIntegrityState:
    prefix = "listing-integrity:"
    if not assessment.assessment_kind.startswith(prefix):
        raise ValidationError("assessment is not a listing-integrity specialization")
    try:
        return ListingIntegrityState(assessment.assessment_kind.removeprefix(prefix))
    except ValueError as exc:
        raise ValidationError("unknown listing-integrity assessment kind") from exc


def propose_listing_integrity_work(
    kernel: ResponsibilityKernel,
    assessment: ResponsibilityAssessment,
    *,
    proposal_id: str,
) -> WorkProposal | None:
    """Persist a bounded portable WorkProposal; proposal creation is not Work admission."""

    state = _assessment_state(assessment)
    proposal = listing_integrity_proposal(assessment, state=state, now=assessment.assessed_at)
    if proposal is None:
        return None
    proposal = proposal.model_copy(update={"id": proposal_id})
    return kernel.propose(proposal, now=assessment.assessed_at)


def commit_steward_work(
    kernel: ResponsibilityKernel,
    proposal: WorkProposal,
    *,
    commitment_id: str,
    envelope: ResourceVector,
    committed_at: datetime,
) -> Commitment:
    """Run the explicit priority/portfolio/reservation chain without minting authority."""

    if not proposal.requested_resources.fits_within(envelope):
        raise ValidationError("listing integrity proposal exceeds steward resource envelope")

    priority = PriorityJudgment(
        id=f"priority:{proposal.id}",
        proposal_ref=proposal.id,
        dimensions=PriorityDimensions(
            urgency=3,
            impact=3,
            risk=1,
            reversibility=5,
            confidence=4,
            resource_cost=1,
            human_attention_cost=0,
        ),
        policy_ref=_PORTFOLIO_POLICY,
        admitted=True,
        rationale="bounded Commerce steward work is admissible under the experiment policy",
    )
    if kernel.journal.get(priority.id) is None:
        kernel.record_priority_judgment(priority)

    pool = ResourcePool(
        id=_RESOURCE_POOL_ID,
        pool_key="commerce-listing-integrity",
        capacity=envelope,
        policy_ref=_PORTFOLIO_POLICY,
    )
    existing_pool = kernel.journal.get(pool.id)
    if existing_pool is None:
        kernel.create_resource_pool(pool)
    elif not isinstance(existing_pool, ResourcePool) or existing_pool.capacity != envelope:
        raise ValidationError("steward resource pool already exists with a different envelope")

    portfolio = PortfolioAdmissionDecision(
        id=f"portfolio:{proposal.id}",
        proposal_ref=proposal.id,
        resource_pool_ref=pool.id,
        policy_ref=_PORTFOLIO_POLICY,
        admitted=True,
        rationale="explicit resource envelope admits the proposal",
    )
    if kernel.journal.get(portfolio.id) is None:
        kernel.record_portfolio_admission(portfolio)

    reservation = ResourceReservation(
        id=f"reservation:{proposal.id}",
        responsibility_ref=proposal.responsibility_ref,
        proposal_ref=proposal.id,
        resource_pool_ref=pool.id,
        resources=proposal.requested_resources,
        reserved_at=committed_at,
    )
    if kernel.journal.get(reservation.id) is None:
        kernel.reserve(reservation, now=committed_at)

    commitment = Commitment(
        id=commitment_id,
        responsibility_ref=proposal.responsibility_ref,
        responsibility_version=proposal.responsibility_version,
        proposal_ref=proposal.id,
        priority_judgment_ref=priority.id,
        portfolio_admission_ref=portfolio.id,
        reservation_ref=reservation.id,
        resources=proposal.requested_resources,
        committed_at=committed_at,
        stop_conditions=list(proposal.stop_conditions),
        escalation_conditions=list(proposal.escalation_conditions),
    )
    return kernel.commit(commitment, now=committed_at)


def route_external_repair() -> EscalationRoute:
    """External listing mutation remains on Commerce Decision/Authorization."""

    return EscalationRoute.HUMAN_DECISION_REQUIRED


def complete_steward_work(
    kernel: ResponsibilityKernel,
    commitment: Commitment,
    *,
    run: Run,
    verification_refs: list[str],
) -> tuple[str, ResponsibilityStatus]:
    """Complete bounded Work only through the portable terminal authority gate."""

    work = kernel.materialize_work(commitment.id)
    if run.work_id != work.id:
        raise ValidationError("steward run must bind the materialized responsibility Work")
    existing_run = kernel.store.get_run(run.id)
    if existing_run is None:
        kernel.store.save_run(run)
    elif existing_run.work_id != work.id:
        raise ValidationError("steward run id is already bound to another Work")
    CompletionAuthority(kernel.store).authorize(
        work=work,
        run=run,
        verification_refs=verification_refs,
    )
    return work.id, kernel.current_status(commitment.responsibility_ref)


__all__ = [
    "EscalationRoute",
    "IntegrityAssessmentKind",
    "LISTING_INTEGRITY_MISSION",
    "ListingIntegritySnapshot",
    "MissionStatus",
    "ShopifyReadback",
    "StewardWorkKind",
    "assess_listing_integrity",
    "build_listing_integrity_snapshot",
    "commit_steward_work",
    "complete_steward_work",
    "ensure_listing_integrity_responsibility",
    "propose_listing_integrity_work",
    "route_external_repair",
]
