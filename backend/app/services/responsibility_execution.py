"""Commerce execution-authority specializations for bounded domain workflows.

The module keeps Decision and ExecutionAuthorization distinct.  Profiles are
server-owned and bind authority to the exact subject/version/fingerprint and
operation set.  Effect planning consumes the matching authorization and the
physical dispatch boundary revalidates the same current facts.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from portable_runtime.public_contracts.experience import (
    evaluate_experience_use_contract,
    get_historical_experience_use_contract,
)
from portable_runtime.public_contracts.models import ExperienceUseRequirementV1
from sqlalchemy import or_, select

from app.core.errors import NotFoundError, ValidationError
from app.models.effect import EffectLedgerEntry
from app.models.listing import ListingPublication
from app.models.responsibility import ExecutionAuthorization, ResponsibilityBinding
from app.models.returns import ReturnCase
from app.models.workflow import WorkflowRun, WorkItem, WorkItemDecision
from app.responsibility.store import CommerceResponsibilityStore
from app.services.current_use_eligibility import compose_current_use_eligibility
from app.services.publication_qualification import require_current_publication_qualification
from app.services.responsibility import (
    authorization_allows_effect,
    issue_execution_authorization,
)
from app.services.responsibility_profiles import listing_experience_required

LISTING_PUBLICATION_WORKFLOW = "listing-publication"
LISTING_PUBLICATION_EFFECT = "shopify.product_publish"
LISTING_SUBJECT_TYPE = "listing_publication"
RETURN_WORKFLOWS = frozenset({"return", "return-to-refund"})
RETURN_SUBJECT_TYPE = "return_case"
RETURN_ENVIRONMENT_REF = "commerce"

AuthorizationProfileName = Literal[
    "listing-publication-v1",
    "return-credit-note-v1",
    "return-refund-v1",
]


@dataclass(frozen=True)
class WorkItemAuthorizationProfile:
    name: AuthorizationProfileName
    target_system: str
    allowed_operations: tuple[str, ...]
    policy_version: str


LISTING_PROFILE = WorkItemAuthorizationProfile(
    name="listing-publication-v1",
    target_system="shopify",
    allowed_operations=(LISTING_PUBLICATION_EFFECT,),
    policy_version="listing-publication-v1",
)
RETURN_CREDIT_NOTE_PROFILE = WorkItemAuthorizationProfile(
    name="return-credit-note-v1",
    target_system="odoo",
    allowed_operations=("odoo.credit_note_create", "odoo.credit_note_validate"),
    policy_version="return-credit-note-v1",
)
RETURN_REFUND_PROFILE = WorkItemAuthorizationProfile(
    name="return-refund-v1",
    target_system="shopify",
    allowed_operations=("shopify.refund_create",),
    policy_version="return-refund-v1",
)


@dataclass(frozen=True)
class ListingAuthorizationSubject:
    subject_type: str
    subject_ref: str
    subject_version: str
    subject_fingerprint: str
    policy_version: str
    environment_ref: str


@dataclass(frozen=True)
class ReturnAuthorizationSubject:
    subject_type: str
    subject_ref: str
    subject_version: str
    subject_fingerprint: str
    policy_version: str
    environment_ref: str
    scope: dict[str, object]


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


def _canonical_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _listing_fingerprint(listing: ListingPublication) -> str:
    """Fingerprint publication-relevant subject content, excluding lifecycle state."""

    return _canonical_fingerprint(
        {
            "id": str(listing.id),
            "sku": listing.sku,
            "channel": listing.channel,
            "version": listing.version,
            "payload": listing.payload or {},
        }
    )


def listing_authorization_subject(db, item: WorkItem) -> ListingAuthorizationSubject:
    """Resolve the exact subject/context of a listing-publication approval item."""

    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise ValidationError("work item references missing workflow")
    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW:
        raise ValidationError("listing authorization requires listing-publication workflow")
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


def _return_case_for_item(db, item: WorkItem) -> tuple[WorkflowRun, ReturnCase]:
    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise ValidationError("work item references missing workflow")
    if run.workflow_type not in RETURN_WORKFLOWS:
        raise ValidationError("return authorization requires a ReturnCase workflow")
    raw = (item.payload_json or {}).get("case_id")
    if raw is None:
        raise ValidationError("return financial work item is missing case_id")
    try:
        case_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise ValidationError("return financial work item has invalid case_id") from exc
    case = db.get(ReturnCase, case_id)
    if case is None:
        raise NotFoundError("return case not found")
    return run, case


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def return_authorization_subject(
    db,
    item: WorkItem,
    profile: WorkItemAuthorizationProfile,
) -> ReturnAuthorizationSubject:
    """Bind authority to approved financial inputs, never execution outputs/state."""

    run, case = _return_case_for_item(db, item)
    scope: dict[str, object] = {
        "return_ref": case.return_ref,
        "order_ref": case.order_ref,
        "shopify_order_id": case.shopify_order_id,
        "refund_amount": _decimal_text(case.refund_amount),
        "currency": case.currency,
        "disposition": None if case.disposition is None else case.disposition.value,
        "workflow_ref": str(run.id),
    }
    fingerprint = _canonical_fingerprint(
        {
            "subject_ref": str(case.id),
            **scope,
        }
    )
    return ReturnAuthorizationSubject(
        subject_type=RETURN_SUBJECT_TYPE,
        subject_ref=str(case.id),
        subject_version=str(case.version),
        subject_fingerprint=fingerprint,
        policy_version=profile.policy_version,
        environment_ref=RETURN_ENVIRONMENT_REF,
        scope=scope,
    )


def authorization_profile_for_work_item(
    db,
    item: WorkItem,
) -> WorkItemAuthorizationProfile | None:
    """Resolve the server-owned execution-authority profile for one work item."""

    run = db.get(WorkflowRun, item.workflow_id)
    if run is None:
        raise ValidationError("work item references missing workflow")
    step = str((item.payload_json or {}).get("next_step") or "approve")
    if run.workflow_type == LISTING_PUBLICATION_WORKFLOW and step == "approve":
        return LISTING_PROFILE
    if run.workflow_type in RETURN_WORKFLOWS and step == "approve_credit_note":
        return RETURN_CREDIT_NOTE_PROFILE
    if run.workflow_type in RETURN_WORKFLOWS and step == "approve_refund":
        return RETURN_REFUND_PROFILE
    return None


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
    item.payload_json = {
        **(item.payload_json or {}),
        "authorization_ref": str(authorization.id),
        "authorization_profile": LISTING_PROFILE.name,
    }
    db.flush()
    return authorization


def issue_work_item_execution_authorization(
    db,
    *,
    decision: WorkItemDecision,
    item: WorkItem,
    actor_user_id: uuid.UUID,
    listing_scope: dict[str, object] | None = None,
    expires_at: dt.datetime | None = None,
) -> tuple[ExecutionAuthorization, WorkItemAuthorizationProfile]:
    """Mint only the profile implied by workflow type + next_step + server facts."""

    profile = authorization_profile_for_work_item(db, item)
    if profile is None:
        raise ValidationError("work item does not have an execution-authorization profile")
    if profile is LISTING_PROFILE:
        authorization = issue_listing_execution_authorization(
            db,
            decision=decision,
            item=item,
            actor_user_id=actor_user_id,
            scope=dict(listing_scope or {"purpose": "publish", "channel": "shopify"}),
            expires_at=expires_at,
        )
        return authorization, profile

    subject = return_authorization_subject(db, item, profile)
    authorization = issue_execution_authorization(
        db,
        decision_ref=decision.id,
        subject_type=subject.subject_type,
        subject_ref=subject.subject_ref,
        subject_version=subject.subject_version,
        subject_fingerprint=subject.subject_fingerprint,
        target_system=profile.target_system,
        allowed_operations=list(profile.allowed_operations),
        scope=dict(subject.scope),
        policy_version=subject.policy_version,
        environment_ref=subject.environment_ref,
        issued_by_user_id=actor_user_id,
        expires_at=expires_at,
    )
    item.payload_json = {
        **(item.payload_json or {}),
        "authorization_ref": str(authorization.id),
        "authorization_profile": profile.name,
    }
    db.flush()
    return authorization, profile


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


def _current_publication_qualification(
    db,
    listing: ListingPublication,
) -> tuple[bool, str | None]:
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
        return (
            False,
            "unavailable",
            binding.historical_use_ref,
            ("historical-experience-use-missing",),
        )
    if (
        historical.id != binding.historical_use_ref
        or historical.requirement_digest != binding.requirement_digest
        or historical.snapshot_digest != binding.snapshot_digest
    ):
        return (
            False,
            "unavailable",
            binding.historical_use_ref,
            ("historical-experience-binding-mismatch",),
        )

    try:
        snapshot = json.loads(historical.snapshot_semantic_json)
        requirement_payload = snapshot["requirement"]
        requirement = ExperienceUseRequirementV1.model_validate(requirement_payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, "unavailable", historical.id, ("historical-experience-requirement-invalid",)

    admission = evaluate_experience_use_contract(store, requirement)
    if admission.requirement_digest != binding.requirement_digest:
        return False, "unavailable", historical.id, ("current-experience-requirement-drift",)
    current_use = compose_current_use_eligibility(
        db,
        admission=admission,
        requirement=requirement,
    )
    if not current_use.eligible:
        reasons = current_use.reasons or (f"current-experience-{current_use.status}",)
        return False, current_use.status, historical.id, reasons
    return True, current_use.status, historical.id, ()


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
    (
        experience_current,
        experience_status,
        historical_ref,
        experience_reasons,
    ) = _current_experience_use(
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


def _items_for_run(db, run: WorkflowRun) -> list[WorkItem]:
    return list(
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == run.id)
            .order_by(WorkItem.created_at.desc())
        ).scalars()
    )


def _authorization_from_item(db, item: WorkItem) -> ExecutionAuthorization | None:
    auth_ref = (item.payload_json or {}).get("authorization_ref")
    if not auth_ref:
        return None
    try:
        authorization_id = uuid.UUID(str(auth_ref))
    except ValueError as exc:
        raise ValidationError("work item authorization_ref is invalid") from exc
    authorization = db.get(ExecutionAuthorization, authorization_id)
    if authorization is None:
        raise ValidationError("work item references missing ExecutionAuthorization")
    return authorization


def _legacy_unbound_effect_exists(db, run: WorkflowRun, operation: str) -> bool:
    """Preserve pre-C18 rows: an already-recorded unbound effect is not backfilled."""

    target_system, _, op = operation.partition(".")
    if not target_system or not op:
        return False
    existing = (
        db.execute(
            select(EffectLedgerEntry).where(
                or_(
                    EffectLedgerEntry.workflow_ref == run.id,
                    EffectLedgerEntry.approval_ref == run.id,
                ),
                EffectLedgerEntry.target_system == target_system,
                EffectLedgerEntry.operation == op,
                EffectLedgerEntry.authorization_ref.is_(None),
            )
        )
        .scalars()
        .first()
    )
    return existing is not None


def _resolve_return_authorization(
    db,
    *,
    run: WorkflowRun,
    operation: str,
) -> ExecutionAuthorization | None:
    profile = (
        RETURN_CREDIT_NOTE_PROFILE
        if operation in RETURN_CREDIT_NOTE_PROFILE.allowed_operations
        else RETURN_REFUND_PROFILE
        if operation in RETURN_REFUND_PROFILE.allowed_operations
        else None
    )
    if profile is None:
        return None
    expected_step = (
        "approve_credit_note" if profile is RETURN_CREDIT_NOTE_PROFILE else "approve_refund"
    )
    item = next(
        (
            candidate
            for candidate in _items_for_run(db, run)
            if str((candidate.payload_json or {}).get("next_step") or "") == expected_step
        ),
        None,
    )
    if item is None:
        if _legacy_unbound_effect_exists(db, run, operation):
            return None
        raise ValidationError(f"{profile.name} requires its financial approval work item")

    authorization = _authorization_from_item(db, item)
    if authorization is None:
        if _legacy_unbound_effect_exists(db, run, operation):
            return None
        raise ValidationError(
            f"{profile.name} requires explicit ExecutionAuthorization; Decision alone is insufficient"
        )

    subject = return_authorization_subject(db, item, profile)
    decision = db.execute(
        select(WorkItemDecision).where(WorkItemDecision.work_item_id == item.id)
    ).scalar_one_or_none()
    current = bool(
        decision
        and authorization.decision_ref == decision.id
        and authorization.workflow_ref == run.id
        and authorization.policy_version == profile.policy_version
        and authorization.target_system == profile.target_system
        and set(authorization.allowed_operations) == set(profile.allowed_operations)
        and dict(authorization.scope or {}) == subject.scope
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
    if not current:
        raise ValidationError(
            "ExecutionAuthorization is absent, stale, expired, revoked, rebound, or wrong-scope"
        )
    return authorization


def resolve_effect_authorization(
    db,
    *,
    workflow_ref: uuid.UUID | None,
    operation: str,
) -> ExecutionAuthorization | None:
    """Resolve and revalidate the bounded authority profile for an effect."""

    if workflow_ref is None:
        return None
    run = db.get(WorkflowRun, workflow_ref)
    if run is None:
        raise ValidationError("effect workflow_ref does not resolve to a workflow")

    if run.workflow_type in RETURN_WORKFLOWS:
        return _resolve_return_authorization(db, run=run, operation=operation)

    if run.workflow_type != LISTING_PUBLICATION_WORKFLOW or operation != LISTING_PUBLICATION_EFFECT:
        return None

    item = next(
        (candidate for candidate in _items_for_run(db, run) if (candidate.payload_json or {}).get("listing_id")),
        None,
    )
    if item is None:
        raise ValidationError("listing publication requires its approval work item")
    authorization = _authorization_from_item(db, item)
    if authorization is None:
        raise ValidationError(
            "listing publication requires explicit ExecutionAuthorization; Decision alone is insufficient"
        )

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
    "AuthorizationProfileName",
    "CommercePublicationDispatchEligibility",
    "LISTING_PUBLICATION_EFFECT",
    "LISTING_PUBLICATION_WORKFLOW",
    "ListingAuthorizationSubject",
    "RETURN_CREDIT_NOTE_PROFILE",
    "RETURN_REFUND_PROFILE",
    "ReturnAuthorizationSubject",
    "WorkItemAuthorizationProfile",
    "authorization_profile_for_work_item",
    "issue_listing_execution_authorization",
    "issue_work_item_execution_authorization",
    "listing_authorization_subject",
    "publication_dispatch_eligibility",
    "resolve_effect_authorization",
    "return_authorization_subject",
]
