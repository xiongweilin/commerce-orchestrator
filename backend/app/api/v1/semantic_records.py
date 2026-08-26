"""Append-only semantic/responsibility record endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.core.errors import ConflictError, NotFoundError
from app.models.effect_realization import EffectRealizationStatus
from app.models.reconciliation_resolution import (
    ReconciliationResolutionKind,
    ReconciliationVerificationStatus,
)
from app.models.responsibility import ResponsibilityBinding
from app.models.workflow import WorkflowRun
from app.responsibility.store import CommerceResponsibilityStore
from app.schemas.events import ROLES
from app.services.realization_resolution import (
    append_effect_realization_assessment,
    append_reconciliation_resolution,
)
from app.services.responsibility import (
    confirm_effect_outcome,
    current_open_obligations_for_projections,
    discharge_responsibility_obligation,
    open_responsibility_obligation,
    workflow_responsibility_view,
)
from portable_runtime.public_contracts.experience import (
    commit_historical_experience_use_contract,
    evaluate_experience_use_contract,
)
from portable_runtime.public_contracts.models import (
    ExperienceUseRequirementV1,
    HistoricalExperienceUseCommitV1,
)
from portable_runtime.records.knowledge import KnowledgeProjection

router = APIRouter(prefix="/v1", tags=["semantic-records"])


class EffectRealizationCreate(BaseModel):
    effect_id: uuid.UUID
    realization_status: EffectRealizationStatus
    evidence_refs: list[str] = Field(default_factory=list)


class ResponsibilityObligationCreate(BaseModel):
    workflow_ref: uuid.UUID | None = None
    subject_ref: str = Field(min_length=1, max_length=192)
    source_kind: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=192)
    reason: str = Field(min_length=1, max_length=1024)
    scope: dict[str, Any] = Field(default_factory=dict)
    projection_refs: list[str] = Field(default_factory=list)


class ReconciliationResponsibilityCreate(BaseModel):
    workflow_ref: uuid.UUID | None = None
    subject_ref: str = Field(min_length=1, max_length=192)
    reason: str = Field(min_length=1, max_length=1024)
    scope: dict[str, Any] = Field(default_factory=dict)
    projection_refs: list[str] = Field(default_factory=list)


class ReconciliationResolutionCreate(BaseModel):
    reconciliation_diff_id: uuid.UUID
    resolution_kind: ReconciliationResolutionKind
    verification_status: ReconciliationVerificationStatus
    basis_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    responsibility: ReconciliationResponsibilityCreate | None = None


class ConfirmedOutcomeCreate(BaseModel):
    effect_id: uuid.UUID
    realization_assessment_id: uuid.UUID
    outcome_type: str = Field(min_length=1, max_length=96)


class ExperienceUseEvaluate(BaseModel):
    requirement: ExperienceUseRequirementV1


class HistoricalExperienceBind(BaseModel):
    workflow_ref: uuid.UUID | None = None
    subject_type: str = Field(min_length=1, max_length=64)
    subject_ref: str = Field(min_length=1, max_length=192)
    subject_version: str = Field(min_length=1, max_length=192)
    commit: HistoricalExperienceUseCommitV1


class KnowledgeProjectionCreate(BaseModel):
    projection: dict[str, Any]


def _obligation_view(row) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "workflowRef": None if row.workflow_ref is None else str(row.workflow_ref),
        "subjectRef": row.subject_ref,
        "sourceKind": row.source_kind,
        "sourceRef": row.source_ref,
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "reason": row.reason,
        "scope": row.scope,
        "projectionRefs": list(row.projection_refs or []),
    }


@router.post("/effect-realizations")
def create_effect_realization(
    body: EffectRealizationCreate,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("system_admin"))],
) -> dict[str, object]:
    assessment = append_effect_realization_assessment(
        db,
        effect_id=body.effect_id,
        realization_status=body.realization_status,
        assessed_by_user_id=user_id,
        evidence_refs=body.evidence_refs,
    )
    return {
        "assessmentId": str(assessment.id),
        "effectId": str(assessment.effect_id),
        "realizationStatus": assessment.realization_status.value,
        "assessedByUserId": str(assessment.assessed_by_user_id),
        "assessedAt": assessment.assessed_at.isoformat(),
    }


@router.post("/reconciliation-resolutions")
def create_reconciliation_resolution(
    body: ReconciliationResolutionCreate,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("accountant", "system_admin"))],
) -> dict[str, object]:
    resolution = append_reconciliation_resolution(
        db,
        reconciliation_diff_id=body.reconciliation_diff_id,
        resolution_kind=body.resolution_kind,
        verification_status=body.verification_status,
        recorded_by_user_id=user_id,
        basis_refs=body.basis_refs,
        evidence_refs=body.evidence_refs,
    )
    obligation = None
    if body.responsibility is not None:
        responsibility = body.responsibility
        obligation = open_responsibility_obligation(
            db,
            workflow_ref=responsibility.workflow_ref,
            subject_ref=responsibility.subject_ref,
            source_kind="reconciliation_resolution",
            source_ref=str(resolution.id),
            reason=responsibility.reason,
            scope=responsibility.scope,
            projection_refs=responsibility.projection_refs,
            recorded_by_user_id=user_id,
        )
    return {
        "resolutionId": str(resolution.id),
        "reconciliationDiffId": str(resolution.reconciliation_diff_id),
        "resolutionKind": resolution.resolution_kind.value,
        "verificationStatus": resolution.verification_status.value,
        "recordedByUserId": str(resolution.recorded_by_user_id),
        "recordedAt": resolution.recorded_at.isoformat(),
        "responsibilityObligation": None if obligation is None else _obligation_view(obligation),
    }


@router.post("/responsibility/confirmed-outcomes")
def create_confirmed_outcome(
    body: ConfirmedOutcomeCreate,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("compliance", "system_admin"))],
) -> dict[str, Any]:
    outcome = confirm_effect_outcome(
        db,
        effect_id=body.effect_id,
        realization_assessment_id=body.realization_assessment_id,
        outcome_type=body.outcome_type,
        confirmed_by_user_id=user_id,
    )
    return {
        "id": str(outcome.id),
        "effectId": str(outcome.effect_id),
        "realizationAssessmentId": str(outcome.realization_assessment_id),
        "outcomeType": outcome.outcome_type,
        "verificationRefs": list(outcome.evidence_refs),
        "confirmedAt": outcome.confirmed_at.isoformat(),
    }


@router.post("/responsibility/obligations")
def create_responsibility_obligation(
    body: ResponsibilityObligationCreate,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("compliance", "system_admin"))],
) -> dict[str, Any]:
    row = open_responsibility_obligation(
        db,
        workflow_ref=body.workflow_ref,
        subject_ref=body.subject_ref,
        source_kind=body.source_kind,
        source_ref=body.source_ref,
        reason=body.reason,
        scope=body.scope,
        projection_refs=body.projection_refs,
        recorded_by_user_id=user_id,
    )
    return _obligation_view(row)


@router.post("/responsibility/obligations/{obligation_id}/discharge")
def discharge_obligation(
    obligation_id: uuid.UUID,
    db: Annotated[Session, Depends(get_session)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("compliance", "system_admin"))],
) -> dict[str, Any]:
    row = discharge_responsibility_obligation(
        db,
        obligation_id=obligation_id,
        actor_user_id=user_id,
    )
    return _obligation_view(row)


@router.post("/responsibility/knowledge-projections")
def create_knowledge_projection(
    body: KnowledgeProjectionCreate,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("compliance", "system_admin"))],
) -> dict[str, Any]:
    projection = KnowledgeProjection.model_validate(body.projection)
    store = CommerceResponsibilityStore(db)
    store.save_knowledge_projection(projection)
    return projection.model_dump(mode="json")


@router.post("/responsibility/experience/evaluate")
def evaluate_experience_use(
    body: ExperienceUseEvaluate,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*ROLES))],
) -> dict[str, Any]:
    store = CommerceResponsibilityStore(db)
    admission = evaluate_experience_use_contract(store, body.requirement)
    obligations = current_open_obligations_for_projections(db, body.requirement.projection_refs)
    return {
        "admission": admission.model_dump(mode="json", by_alias=True),
        "openResponsibilities": [_obligation_view(row) for row in obligations],
        "authorityBearing": False,
    }


@router.post("/responsibility/experience/bind")
def bind_historical_experience_use(
    body: HistoricalExperienceBind,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("compliance", "system_admin"))],
) -> dict[str, Any]:
    if body.workflow_ref is not None and db.get(WorkflowRun, body.workflow_ref) is None:
        raise NotFoundError("historical experience binding workflow not found")
    store = CommerceResponsibilityStore(db)
    historical = commit_historical_experience_use_contract(store, body.commit)
    existing = db.execute(
        select(ResponsibilityBinding).where(
            ResponsibilityBinding.historical_use_ref == historical.id
        )
    ).scalar_one_or_none()
    expected = {
        "workflow_ref": body.workflow_ref,
        "subject_type": body.subject_type,
        "subject_ref": body.subject_ref,
        "subject_version": body.subject_version,
        "judgment_ref": historical.judgment_ref,
        "requirement_digest": historical.requirement_digest,
        "snapshot_digest": historical.snapshot_digest,
    }
    if existing is None:
        existing = ResponsibilityBinding(
            historical_use_ref=historical.id,
            **expected,
        )
        db.add(existing)
        db.flush()
    else:
        actual = {key: getattr(existing, key) for key in expected}
        if actual != expected:
            raise ConflictError("historical ExperienceUse sidecar binding rebound refused")
    return {
        "historicalUse": historical.model_dump(mode="json"),
        "commerceBinding": {
            "id": str(existing.id),
            "workflowRef": None if existing.workflow_ref is None else str(existing.workflow_ref),
            "subjectType": existing.subject_type,
            "subjectRef": existing.subject_ref,
            "subjectVersion": existing.subject_version,
        },
    }


@router.get("/workflows/{workflow_id}/responsibility")
def get_workflow_responsibility(
    workflow_id: uuid.UUID,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*ROLES))],
) -> dict[str, Any]:
    return workflow_responsibility_view(db, workflow_id)
