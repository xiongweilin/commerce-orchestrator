"""Commerce execution-authority specialization for publication responsibility.

The module binds one explicit :class:`ExecutionAuthorization` to the exact
listing-publication subject that a durable work-item decision concerns. It
also composes current publication qualification and, when Commerce provenance
requires it, current Experience Use eligibility. The composition is read-only:
it never derives or mints Decision, Authorization, or Effect authority.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass

from portable_runtime.public_contracts.experience import (
    evaluate_experience_use_contract,
    get_historical_experience_use_contract,
)
from portable_runtime.public_contracts.models import ExperienceUseRequirementV1
from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.models.listing import ListingPublication
from app.models.responsibility import ExecutionAuthorization, ResponsibilityBinding
from app.models.workflow import WorkflowRun, WorkItem, WorkItemDecision
from app.responsibility.store import CommerceResponsibilityStore
from app.services.publication_qualification import require_current_publication_qualification
from app.services.responsibility import (
    authorization_allows_effect,
    issue_execution_authorization,
)
from app.services.responsibility_profiles import listing_experience_required

LISTING_PUBLICATION_WORKFLOW = "listing-publication"
LISTING_PUBLICATION_EFFECT = "shopify.product_publish"
LISTING_SUBJECT_TYPE = "listing_publication"


@dataclass(frozen=True)
class ListingAuthorizationSubject:
    subject_type: str
    subject_ref: str
    subject_version: str
    subject_fingerprint: str
    policy_version: str
    environment_ref: str


@dataclass(frozen=True)
class CommercePublicationDispatchEligibility:
    """Non-authority-bearing composition of independent publication gates."""

    eligible: bool
    authorization_current: bool
    publication_qualification_current: bool
    experience_required: bool
    experience_status: str
    historical_use_ref: str | None
    reasons: tuple[str, ...]
    authority_bearing: bool = False


def _listing_fingerprint(listing: ListingPublication) -> str:
    """Fingerprint publication-relevant subject content, excluding lifecycle state."""

    payload = {
        "id": str(listing.id),
        "sku": listing.sku,
        "channel": listing.channel,
        "version": listing.version,
        "payload": listing.payload or {},
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def listing_authorization_subject(db, item: WorkItem) -> ListingAuthorizationSubject:
    """Resolve the exact subject/context of a listing-publication approval item."""

    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise ValidationError("work item references missing workflow")
    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW:
        raise ValidationError("authorized-decision pilot supports listing-publication only")
    payload = item.payload_json or {}
    listing_ref = payload.get("listing_id")
    if not listing_ref:
        raise ValidationError("listing-publication work item is missing listing_id")
    try:
        listing_id = uuid.UUID(str(listing_ref))
    except ValueError as exc:
        raise ValidationError("listing-publication work item has invalid listing_id") from exc
    listing = db.get(ListingPublication, listing_id)
    if listing is None:
        raise NotFoundError("listing publication not found")
    listing_payload = listing.payload or {}
    qualification = listing_payload.get("qualification_context")
    if not isinstance(qualification, dict):
        raise ValidationError("listing publication is missing qualification context")
    policy_version = str(qualification.get("policy_version") or "").strip()
    environment_ref = str(qualification.get("environment_ref") or "").strip()
    if not policy_version or not environment_ref:
        raise ValidationError("listing qualification policy/environment binding is incomplete")
    return ListingAuthorizationSubject(
        subject_type=LISTING_SUBJECT_TYPE,
        subject_ref=str(listing.id),
        subject_version=str(listing.version),
        subject_fingerprint=_listing_fingerprint(listing),
        policy_version=policy_version,
        environment_ref=environment_ref,
    )


def issue_listing_execution_authorization(
    db,
    *,
    decision: WorkItemDecision,
    item: WorkItem,
    actor_user_id: uuid.UUID,
    scope: dict[str, object],
    expires_at: dt.datetime | None = None,
) -> ExecutionAuthorization:
    """Explicitly mint and attach the exact publication authorization."""

    subject = listing_authorization_subject(db, item)
    authorization = issue_execution_authorization(
        db,
        decision_ref=decision.id,
        subject_type=subject.subject_type,
        subject_ref=subject.subject_ref,
        subject_version=subject.subject_version,
        subject_fingerprint=subject.subject_fingerprint,
        target_system="shopify",
        allowed_operations=[LISTING_PUBLICATION_EFFECT],
        scope=dict(scope),
        policy_version=subject.policy_version,
        environment_ref=subject.environment_ref,
        issued_by_user_id=actor_user_id,
        expires_at=expires_at,
    )
    # JSON columns do not reliably detect in-place mutation; assign a new dict.
    item.payload_json = {**(item.payload_json or {}), "authorization_ref": str(authorization.id)}
    db.flush()
    return authorization


def _listing_for_item(db, item: WorkItem) -> ListingPublication:
    raw = (item.payload_json or {}).get("listing_id")
    if raw is None:
        raise ValidationError("listing-publication work item is missing listing_id")
    try:
        listing_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise ValidationError("listing-publication work item has invalid listing_id") from exc
    listing = db.get(ListingPublication, listing_id)
    if listing is None:
        raise NotFoundError("listing publication not found")
    return listing


def _current_publication_qualification(db, listing: ListingPublication) -> tuple[bool, str | None]:
    payload = listing.payload or {}
    raw_revision_id = payload.get("catalog_revision_id") or payload.get("revision_id")
    qualification = payload.get("qualification_context")
    if raw_revision_id is None or not isinstance(qualification, dict):
        return False, "publication qualification binding is required"
    try:
        revision_id = uuid.UUID(str(raw_revision_id))
    except ValueError:
        return False, "publication qualification revision id is invalid"
    try:
        require_current_publication_qualification(
            db,
            catalog_revision_id=revision_id,
            expected_sku=listing.sku,
            channel=listing.channel,
            purpose=str(qualification.get("purpose") or ""),
            policy_version=str(qualification.get("policy_version") or ""),
            adapter_version=str(qualification.get("adapter_version") or ""),
            environment_ref=str(qualification.get("environment_ref") or ""),
        )
    except ValidationError as exc:
        return False, str(exc)
    return True, None


def _current_experience_use(
    db,
    *,
    run: WorkflowRun,
    listing: ListingPublication,
    subject: ListingAuthorizationSubject,
) -> tuple[bool, str, str | None, tuple[str, ...]]:
    required = listing_experience_required(db, listing)
    if not required:
        return True, "not-required", None, ()

    bindings = list(
        db.execute(
            select(ResponsibilityBinding)
            .where(
                ResponsibilityBinding.workflow_ref == run.id,
                ResponsibilityBinding.subject_type == subject.subject_type,
                ResponsibilityBinding.subject_ref == subject.subject_ref,
                ResponsibilityBinding.subject_version == subject.subject_version,
            )
            .order_by(ResponsibilityBinding.created_at.desc())
        ).scalars()
    )
    if not bindings:
        return False, "unavailable", None, ("historical-experience-binding-required",)
    if len(bindings) != 1:
        return False, "unavailable", None, ("ambiguous-historical-experience-binding",)
    binding = bindings[0]

    store = CommerceResponsibilityStore(db)
    historical = get_historical_experience_use_contract(store, binding.judgment_ref)
    if historical is None:
        return False, "unavailable", binding.historical_use_ref, ("historical-experience-use-missing",)
    if (
        historical.id != binding.historical_use_ref
        or historical.requirement_digest != binding.requirement_digest
        or historical.snapshot_digest != binding.snapshot_digest
    ):
        return False, "unavailable", binding.historical_use_ref, ("historical-experience-binding-mismatch",)

    try:
        snapshot = json.loads(historical.snapshot_semantic_json)
        requirement_payload = snapshot["requirement"]
        requirement = ExperienceUseRequirementV1.model_validate(requirement_payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, "unavailable", historical.id, ("historical-experience-requirement-invalid",)

    admission = evaluate_experience_use_contract(store, requirement)
    if admission.requirement_digest != binding.requirement_digest:
        return False, "unavailable", historical.id, ("current-experience-requirement-drift",)
    if admission.status != "allowed":
        reasons = tuple(admission.reasons) or (f"current-experience-{admission.status}",)
        return False, admission.status, historical.id, reasons
    return True, admission.status, historical.id, ()


def publication_dispatch_eligibility(
    db,
    *,
    run: WorkflowRun,
    item: WorkItem,
    authorization: ExecutionAuthorization,
    operation: str,
) -> CommercePublicationDispatchEligibility:
    """Evaluate all current listing gates without creating authority or effects."""

    subject = listing_authorization_subject(db, item)
    listing = _listing_for_item(db, item)
    reasons: list[str] = []

    authorization_current = (
        authorization.workflow_ref == run.id
        and authorization.policy_version == subject.policy_version
        and authorization_allows_effect(
            authorization,
            operation=operation,
            subject_type=subject.subject_type,
            subject_ref=subject.subject_ref,
            subject_version=subject.subject_version,
            subject_fingerprint=subject.subject_fingerprint,
            environment_ref=subject.environment_ref,
        )
    )
    if not authorization_current:
        reasons.append("execution-authorization-not-current")

    qualification_current, qualification_reason = _current_publication_qualification(db, listing)
    if not qualification_current:
        reasons.append(f"publication-qualification:{qualification_reason}")

    experience_required = listing_experience_required(db, listing)
    experience_current, experience_status, historical_ref, experience_reasons = _current_experience_use(
        db,
        run=run,
        listing=listing,
        subject=subject,
    )
    reasons.extend(f"experience:{reason}" for reason in experience_reasons)

    return CommercePublicationDispatchEligibility(
        eligible=authorization_current and qualification_current and experience_current,
        authorization_current=authorization_current,
        publication_qualification_current=qualification_current,
        experience_required=experience_required,
        experience_status=experience_status,
        historical_use_ref=historical_ref,
        reasons=tuple(reasons),
    )


def resolve_effect_authorization(
    db,
    *,
    workflow_ref: uuid.UUID | None,
    operation: str,
) -> ExecutionAuthorization | None:
    """Resolve and revalidate an explicit authorization for a planned effect.

    Only the listing-publication pilot is responsibility-gated here. Other
    workflow types retain their existing behavior while still receiving
    ``workflow_ref`` provenance on newly recorded effects.
    """

    if workflow_ref is None:
        return None
    run = db.get(WorkflowRun, workflow_ref)
    if run is None:
        raise ValidationError("effect workflow_ref does not resolve to a workflow")
    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW:
        return None
    if operation != LISTING_PUBLICATION_EFFECT:
        return None

    items = list(
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == run.id)
            .order_by(WorkItem.created_at.desc())
        ).scalars()
    )
    item = next(
        (candidate for candidate in items if (candidate.payload_json or {}).get("listing_id")),
        None,
    )
    if item is None:
        raise ValidationError("listing publication requires its approval work item")
    auth_ref = (item.payload_json or {}).get("authorization_ref")
    if not auth_ref:
        raise ValidationError(
            "listing publication requires explicit ExecutionAuthorization; "
            "Decision alone is insufficient"
        )
    try:
        authorization_id = uuid.UUID(str(auth_ref))
    except ValueError as exc:
        raise ValidationError("work item authorization_ref is invalid") from exc
    authorization = db.get(ExecutionAuthorization, authorization_id)
    if authorization is None:
        raise ValidationError("work item references missing ExecutionAuthorization")

    eligibility = publication_dispatch_eligibility(
        db,
        run=run,
        item=item,
        authorization=authorization,
        operation=operation,
    )
    if not eligibility.authorization_current:
        raise ValidationError(
            "ExecutionAuthorization is absent, stale, expired, revoked, or rebound"
        )
    if not eligibility.publication_qualification_current:
        raise ValidationError(
            "listing publication current qualification is not eligible for dispatch"
        )
    if eligibility.experience_required and eligibility.experience_status != "allowed":
        detail = "; ".join(eligibility.reasons) or eligibility.experience_status
        raise ValidationError(f"listing publication current Experience is not eligible: {detail}")
    if not eligibility.eligible:
        raise ValidationError("listing publication responsibility eligibility is not satisfied")
    return authorization


__all__ = [
    "CommercePublicationDispatchEligibility",
    "LISTING_PUBLICATION_EFFECT",
    "LISTING_PUBLICATION_WORKFLOW",
    "ListingAuthorizationSubject",
    "issue_listing_execution_authorization",
    "listing_authorization_subject",
    "publication_dispatch_eligibility",
    "resolve_effect_authorization",
]
