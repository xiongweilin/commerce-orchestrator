"""Append-only M2 semantic judgment endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.models.effect_realization import EffectRealizationStatus
from app.models.reconciliation_resolution import (
    ReconciliationResolutionKind,
    ReconciliationVerificationStatus,
)
from app.services.realization_resolution import (
    append_effect_realization_assessment,
    append_reconciliation_resolution,
)

router = APIRouter(prefix="/v1", tags=["semantic-records"])


class EffectRealizationCreate(BaseModel):
    effect_id: uuid.UUID
    realization_status: EffectRealizationStatus
    evidence_refs: list[str] = Field(default_factory=list)


class ReconciliationResolutionCreate(BaseModel):
    reconciliation_diff_id: uuid.UUID
    resolution_kind: ReconciliationResolutionKind
    verification_status: ReconciliationVerificationStatus
    basis_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


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
    return {
        "resolutionId": str(resolution.id),
        "reconciliationDiffId": str(resolution.reconciliation_diff_id),
        "resolutionKind": resolution.resolution_kind.value,
        "verificationStatus": resolution.verification_status.value,
        "recordedByUserId": str(resolution.recorded_by_user_id),
        "recordedAt": resolution.recorded_at.isoformat(),
    }
