"""Commerce responsibility specializations over existing durable workflow facts."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import or_, select

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.time import utc_now
from app.models.effect import EffectLedgerEntry
from app.models.effect_realization import (
    EffectRealizationAssessment,
    EffectRealizationStatus,
)
from app.models.responsibility import (
    ConfirmedOutcome,
    ExecutionAuthorization,
    ResponsibilityBinding,
    ResponsibilityObligation,
    ResponsibilityObligationStatus,
)
from app.models.workflow import WorkflowRun, WorkItem, WorkItemDecision, WorkItemDecisionType
from app.schemas.events import EFFECT_OPS


def _required_text(value: str | None, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{field} is required")
    return text


def _authorization_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def issue_execution_authorization(
    db,
    *,
    decision_ref: uuid.UUID,
    subject_type: str,
    subject_ref: str,
    subject_version: str | None,
    subject_fingerprint: str | None,
    target_system: str,
    allowed_operations: list[str],
    scope: dict[str, Any],
    policy_version: str,
    environment_ref: str,
    issued_by_user_id: uuid.UUID,
    expires_at: dt.datetime | None = None,
) -> ExecutionAuthorization:
    """Issue one explicit exact-scope authorization from an existing Decision.

    A WorkItemDecision is necessary but never sufficient by itself. The same
    actor who submitted the durable approve/confirm decision explicitly issues
    this first Commerce authorization contract; future delegated issuers need
    a separate contract rather than an implicit role inference.
    """

    decision = db.get(WorkItemDecision, decision_ref)
    if decision is None:
        raise NotFoundError("work item decision not found")
    if decision.decision not in {WorkItemDecisionType.APPROVE, WorkItemDecisionType.CONFIRM}:
        raise ValidationError("only approve/confirm decisions can support execution authorization")
    if decision.user_id != issued_by_user_id:
        raise PermissionDeniedError(
            "execution authorization issuer must be the actor who submitted the durable decision"
        )
    item = db.get(WorkItem, decision.work_item_id)
    if item is None:
        raise ValidationError("decision references missing work item")
    workflow = db.get(WorkflowRun, item.workflow_id)
    if workflow is None:
        raise ValidationError("work item references missing workflow")

    target = _required_text(target_system, "target_system")
    operations = sorted(
        {_required_text(operation, "allowed_operation") for operation in allowed_operations}
    )
    if not operations:
        raise ValidationError("allowed_operations must not be empty")
    for operation in operations:
        if operation not in EFFECT_OPS:
            raise ValidationError(f"unknown effect operation: {operation}")
        if operation.split(".", 1)[0] != target:
            raise ValidationError(
                f"operation {operation!r} does not belong to target_system {target!r}"
            )
    subject_type = _required_text(subject_type, "subject_type")
    subject_ref = _required_text(subject_ref, "subject_ref")
    policy_version = _required_text(policy_version, "policy_version")
    environment_ref = _required_text(environment_ref, "environment_ref")
    if expires_at is not None and expires_at <= utc_now():
        raise ValidationError("execution authorization expiry must be in the future")

    semantic = {
        "decision_ref": str(decision.id),
        "workflow_ref": str(workflow.id),
        "subject_type": subject_type,
        "subject_ref": subject_ref,
        "subject_version": subject_version,
        "subject_fingerprint": subject_fingerprint,
        "target_system": target,
        "allowed_operations": operations,
        "scope": scope,
        "policy_version": policy_version,
        "environment_ref": environment_ref,
        "issued_by_user_id": str(issued_by_user_id),
        "expires_at": None if expires_at is None else expires_at.isoformat(),
    }
    key = _authorization_key(semantic)
    existing = db.execute(
        select(ExecutionAuthorization).where(ExecutionAuthorization.authorization_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    authorization = ExecutionAuthorization(
        authorization_key=key,
        decision_ref=decision.id,
        workflow_ref=workflow.id,
        subject_type=subject_type,
        subject_ref=subject_ref,
        subject_version=subject_version,
        subject_fingerprint=subject_fingerprint,
        target_system=target,
        allowed_operations=operations,
        scope=dict(scope),
        policy_version=policy_version,
        environment_ref=environment_ref,
        issued_by_user_id=issued_by_user_id,
        expires_at=expires_at,
    )
    db.add(authorization)
    db.flush()
    return authorization


def authorization_allows_effect(
    authorization: ExecutionAuthorization,
    *,
    operation: str,
    subject_type: str,
    subject_ref: str,
    subject_version: str | None,
    subject_fingerprint: str | None,
    environment_ref: str,
    now: dt.datetime | None = None,
) -> bool:
    """Exact-match current authorization check; no policy/role inference."""

    now = now or utc_now()
    if authorization.revoked_at is not None:
        return False
    if authorization.expires_at is not None and authorization.expires_at <= now:
        return False
    if operation not in set(authorization.allowed_operations):
        return False
    if operation.split(".", 1)[0] != authorization.target_system:
        return False
    if authorization.subject_type != subject_type or authorization.subject_ref != subject_ref:
        return False
    if authorization.subject_version != subject_version:
        return False
    if authorization.subject_fingerprint != subject_fingerprint:
        return False
    return authorization.environment_ref == environment_ref


def confirm_effect_outcome(
    db,
    *,
    effect_id: uuid.UUID,
    realization_assessment_id: uuid.UUID,
    outcome_type: str,
    confirmed_by_user_id: uuid.UUID,
) -> ConfirmedOutcome:
    """Confirm a business outcome only from an explicit VERIFIED reality assessment."""

    effect = db.get(EffectLedgerEntry, effect_id)
    if effect is None:
        raise NotFoundError("effect not found")
    assessment = db.get(EffectRealizationAssessment, realization_assessment_id)
    if assessment is None:
        raise NotFoundError("effect realization assessment not found")
    if assessment.effect_id != effect.id:
        raise ValidationError("realization assessment belongs to a different effect")
    if assessment.realization_status is not EffectRealizationStatus.VERIFIED:
        raise ValidationError("ConfirmedOutcome requires VERIFIED effect realization")
    if not assessment.evidence_refs:
        raise ValidationError("ConfirmedOutcome requires verification evidence")
    outcome_type = _required_text(outcome_type, "outcome_type")

    existing = db.execute(
        select(ConfirmedOutcome).where(ConfirmedOutcome.effect_id == effect.id)
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.realization_assessment_id != assessment.id
            or existing.outcome_type != outcome_type
            or list(existing.evidence_refs) != list(assessment.evidence_refs)
        ):
            raise ConflictError(
                "confirmed outcome is immutable; conflicting re-confirmation refused"
            )
        return existing

    outcome = ConfirmedOutcome(
        effect_id=effect.id,
        realization_assessment_id=assessment.id,
        outcome_type=outcome_type,
        evidence_refs=list(assessment.evidence_refs),
        confirmed_by_user_id=confirmed_by_user_id,
    )
    db.add(outcome)
    db.flush()
    return outcome


def open_responsibility_obligation(
    db,
    *,
    workflow_ref: uuid.UUID | None = None,
    subject_ref: str,
    source_kind: str,
    source_ref: str,
    reason: str,
    scope: dict[str, Any],
    projection_refs: list[str],
    recorded_by_user_id: uuid.UUID,
) -> ResponsibilityObligation:
    """Open an explicit scoped responsibility; source facts never auto-globalize it."""

    if workflow_ref is not None and db.get(WorkflowRun, workflow_ref) is None:
        raise NotFoundError("responsibility obligation workflow not found")
    obligation = ResponsibilityObligation(
        workflow_ref=workflow_ref,
        subject_ref=_required_text(subject_ref, "subject_ref"),
        source_kind=_required_text(source_kind, "source_kind"),
        source_ref=_required_text(source_ref, "source_ref"),
        status=ResponsibilityObligationStatus.OPEN,
        reason=_required_text(reason, "reason"),
        scope=dict(scope),
        projection_refs=sorted({ref for ref in projection_refs if str(ref).strip()}),
        recorded_by_user_id=recorded_by_user_id,
    )
    db.add(obligation)
    db.flush()
    return obligation


def discharge_responsibility_obligation(
    db,
    *,
    obligation_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> ResponsibilityObligation:
    """Discharge one explicit obligation without rewriting its original recorder."""

    obligation = db.get(ResponsibilityObligation, obligation_id)
    if obligation is None:
        raise NotFoundError("responsibility obligation not found")
    if obligation.status == ResponsibilityObligationStatus.DISCHARGED:
        return obligation
    _ = actor_user_id  # actor is authenticated/auditable at the API boundary
    obligation.status = ResponsibilityObligationStatus.DISCHARGED
    obligation.discharged_at = utc_now()
    db.flush()
    return obligation


def current_open_obligations_for_projections(
    db,
    projection_refs: list[str],
) -> list[ResponsibilityObligation]:
    refs = {ref for ref in projection_refs if str(ref).strip()}
    if not refs:
        return []
    rows = db.execute(
        select(ResponsibilityObligation).where(
            ResponsibilityObligation.status == ResponsibilityObligationStatus.OPEN
        )
    ).scalars()
    return [row for row in rows if refs.intersection(set(row.projection_refs or []))]


def _iso(value: dt.datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def workflow_responsibility_view(db, workflow_id: uuid.UUID) -> dict[str, Any]:
    """Non-authoritative inspector projection over durable Commerce facts."""

    from app.services.responsibility_execution import (
        LISTING_PUBLICATION_WORKFLOW,
        authorization_profile_for_work_item,
        explain_listing_current_responsibility,
    )

    workflow = db.get(WorkflowRun, workflow_id)
    if workflow is None:
        raise NotFoundError("workflow not found")
    items = list(
        db.execute(
            select(WorkItem)
            .where(WorkItem.workflow_id == workflow.id)
            .order_by(WorkItem.created_at, WorkItem.id)
        ).scalars()
    )
    item_by_id = {item.id: item for item in items}
    item_ids = list(item_by_id)
    decisions = (
        []
        if not item_ids
        else list(
            db.execute(
                select(WorkItemDecision)
                .where(WorkItemDecision.work_item_id.in_(item_ids))
                .order_by(WorkItemDecision.created_at, WorkItemDecision.id)
            ).scalars()
        )
    )
    authorizations = list(
        db.execute(
            select(ExecutionAuthorization)
            .where(ExecutionAuthorization.workflow_ref == workflow.id)
            .order_by(ExecutionAuthorization.issued_at, ExecutionAuthorization.id)
        ).scalars()
    )
    authorization_by_id = {row.id: row for row in authorizations}
    effects = list(
        db.execute(
            select(EffectLedgerEntry).where(
                or_(
                    EffectLedgerEntry.workflow_ref == workflow.id,
                    EffectLedgerEntry.approval_ref == workflow.id,
                )
            )
        ).scalars()
    )
    effect_ids = [effect.id for effect in effects]
    assessments = (
        []
        if not effect_ids
        else list(
            db.execute(
                select(EffectRealizationAssessment).where(
                    EffectRealizationAssessment.effect_id.in_(effect_ids)
                )
            ).scalars()
        )
    )
    outcomes = (
        []
        if not effect_ids
        else list(
            db.execute(
                select(ConfirmedOutcome).where(ConfirmedOutcome.effect_id.in_(effect_ids))
            ).scalars()
        )
    )
    bindings = list(
        db.execute(
            select(ResponsibilityBinding)
            .where(ResponsibilityBinding.workflow_ref == workflow.id)
            .order_by(ResponsibilityBinding.created_at, ResponsibilityBinding.id)
        ).scalars()
    )
    responsibility_obligations = list(
        db.execute(
            select(ResponsibilityObligation)
            .where(ResponsibilityObligation.workflow_ref == workflow.id)
            .order_by(ResponsibilityObligation.created_at, ResponsibilityObligation.id)
        ).scalars()
    )
    open_obligations = [
        row
        for row in responsibility_obligations
        if row.status == ResponsibilityObligationStatus.OPEN
    ]

    decision_rows: list[dict[str, Any]] = []
    for row in decisions:
        item = item_by_id.get(row.work_item_id)
        profile = None if item is None else authorization_profile_for_work_item(db, item)
        payload = {} if item is None else (item.payload_json or {})
        next_step = payload.get("next_step")
        if (
            next_step is None
            and profile is not None
            and workflow.workflow_type == LISTING_PUBLICATION_WORKFLOW
        ):
            next_step = "approve"
        decision_rows.append(
            {
                "id": str(row.id),
                "workItemId": str(row.work_item_id),
                "decision": row.decision.value,
                "userId": str(row.user_id),
                "createdAt": row.created_at.isoformat(),
                "nextStep": None if next_step is None else str(next_step),
                "requiredRoles": [] if item is None else list(item.required_roles or []),
                "authorizationProfile": None if profile is None else profile.name,
                "authorityBearing": False,
            }
        )

    current_responsibility: dict[str, Any] | None = None
    if workflow.workflow_type == LISTING_PUBLICATION_WORKFLOW:
        listing_items = [item for item in items if (item.payload_json or {}).get("listing_id")]
        explain_item: WorkItem | None = None
        if len(listing_items) == 1:
            explain_item = listing_items[0]
        else:
            bound_items = [
                item for item in listing_items if (item.payload_json or {}).get("authorization_ref")
            ]
            if len(bound_items) == 1:
                explain_item = bound_items[0]
        if explain_item is None:
            current_responsibility = {
                "kind": "listing-publication",
                "eligible": False,
                "status": "unavailable",
                "authorizationCurrent": False,
                "publicationQualificationCurrent": None,
                "experienceRequired": None,
                "experienceStatus": "unavailable",
                "portableStatus": "unavailable",
                "historicalUseRef": None,
                "requirementDigest": None,
                "applicableObligationRefs": [],
                "reasons": ["listing-responsibility-work-item-ambiguous-or-missing"],
                "authorityBearing": False,
            }
        else:
            raw_auth_ref = (explain_item.payload_json or {}).get("authorization_ref")
            authorization = None
            if raw_auth_ref:
                try:
                    authorization = authorization_by_id.get(uuid.UUID(str(raw_auth_ref)))
                except ValueError:
                    authorization = None
            try:
                explanation = explain_listing_current_responsibility(
                    db,
                    run=workflow,
                    item=explain_item,
                    authorization=authorization,
                )
            except (NotFoundError, ValidationError) as exc:
                current_responsibility = {
                    "kind": "listing-publication",
                    "eligible": False,
                    "status": "unavailable",
                    "authorizationCurrent": False,
                    "publicationQualificationCurrent": None,
                    "experienceRequired": None,
                    "experienceStatus": "unavailable",
                    "portableStatus": "unavailable",
                    "historicalUseRef": None,
                    "requirementDigest": None,
                    "applicableObligationRefs": [],
                    "reasons": [str(exc)],
                    "authorityBearing": False,
                }
            else:
                current_responsibility = {
                    "kind": "listing-publication",
                    "eligible": explanation.eligible,
                    "status": explanation.status,
                    "authorizationCurrent": explanation.authorization_current,
                    "publicationQualificationCurrent": (
                        explanation.publication_qualification_current
                    ),
                    "experienceRequired": explanation.experience_required,
                    "experienceStatus": explanation.experience_status,
                    "portableStatus": explanation.portable_status,
                    "historicalUseRef": explanation.historical_use_ref,
                    "requirementDigest": explanation.requirement_digest,
                    "applicableObligationRefs": list(explanation.applicable_obligation_refs),
                    "reasons": list(explanation.reasons),
                    "authorityBearing": False,
                }

    obligation_rows = [
        {
            "id": str(row.id),
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "createdAt": row.created_at.isoformat(),
            "dischargedAt": _iso(row.discharged_at),
            "recordedByUserId": str(row.recorded_by_user_id),
            "subjectRef": row.subject_ref,
            "sourceKind": row.source_kind,
            "sourceRef": row.source_ref,
            "reason": row.reason,
            "scope": row.scope,
            "projectionRefs": list(row.projection_refs),
        }
        for row in responsibility_obligations
    ]

    return {
        "schema": "commerce-responsibility-inspector-v1",
        "authorityBearing": False,
        "workflow": {
            "id": str(workflow.id),
            "workflowType": workflow.workflow_type,
            "status": workflow.status.value,
            "boundedCompletionOnly": True,
        },
        "currentResponsibility": current_responsibility,
        "historical": [
            {
                "subjectType": row.subject_type,
                "subjectRef": row.subject_ref,
                "subjectVersion": row.subject_version,
                "judgmentRef": row.judgment_ref,
                "historicalUseRef": row.historical_use_ref,
                "requirementDigest": row.requirement_digest,
                "snapshotDigest": row.snapshot_digest,
                "createdAt": row.created_at.isoformat(),
            }
            for row in bindings
        ],
        "decisions": decision_rows,
        "authorizations": [
            {
                "id": str(row.id),
                "decisionRef": str(row.decision_ref),
                "subjectType": row.subject_type,
                "subjectRef": row.subject_ref,
                "subjectVersion": row.subject_version,
                "targetSystem": row.target_system,
                "allowedOperations": list(row.allowed_operations),
                "scope": dict(row.scope or {}),
                "policyVersion": row.policy_version,
                "environmentRef": row.environment_ref,
                "issuedAt": row.issued_at.isoformat(),
                "issuedByUserId": str(row.issued_by_user_id),
                "expiresAt": _iso(row.expires_at),
                "revokedAt": _iso(row.revoked_at),
            }
            for row in authorizations
        ],
        "execution": [
            {
                "effectId": str(row.id),
                "intentId": str(row.intent_id),
                "operation": f"{row.target_system}.{row.operation}",
                "status": row.status.value,
                "workflowRef": None if row.workflow_ref is None else str(row.workflow_ref),
                "authorizationRef": (
                    None if row.authorization_ref is None else str(row.authorization_ref)
                ),
                "legacyApprovalRef": None if row.approval_ref is None else str(row.approval_ref),
            }
            for row in effects
        ],
        "reality": [
            {
                "assessmentId": str(row.id),
                "effectId": str(row.effect_id),
                "status": row.realization_status.value,
                "evidenceRefs": list(row.evidence_refs),
            }
            for row in assessments
        ],
        "confirmedOutcomes": [
            {
                "id": str(row.id),
                "effectId": str(row.effect_id),
                "outcomeType": row.outcome_type,
                "realizationAssessmentRef": str(row.realization_assessment_id),
                "verificationRefs": list(row.evidence_refs),
                "confirmedAt": row.confirmed_at.isoformat(),
                "confirmedByUserId": str(row.confirmed_by_user_id),
                "authorityBearing": False,
            }
            for row in outcomes
        ],
        "responsibilityObligations": obligation_rows,
        "openResponsibility": [
            {
                "id": str(row.id),
                "subjectRef": row.subject_ref,
                "sourceKind": row.source_kind,
                "sourceRef": row.source_ref,
                "reason": row.reason,
                "scope": row.scope,
                "projectionRefs": list(row.projection_refs),
            }
            for row in open_obligations
        ],
        "shortcuts": [
            "Decision != Authorization",
            "ExperienceUseAdmission.allowed != Decision",
            "EffectLedger.succeeded != ConfirmedOutcome",
            "repair disposition != verified repair",
            "workflow completed != universal responsibility discharge",
        ],
    }


__all__ = [
    "authorization_allows_effect",
    "confirm_effect_outcome",
    "current_open_obligations_for_projections",
    "discharge_responsibility_obligation",
    "issue_execution_authorization",
    "open_responsibility_obligation",
    "workflow_responsibility_view",
]
