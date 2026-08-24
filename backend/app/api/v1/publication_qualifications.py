"""Append-only publication qualification assessment API."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.models.publication_qualification import PublicationQualificationStatus
from app.services.publication_qualification import append_publication_qualification_assessment

router = APIRouter(prefix="/v1", tags=["publication-qualification"])


class PublicationQualificationAssessmentCreate(BaseModel):
    catalog_revision_id: uuid.UUID
    channel: str = Field(min_length=1, max_length=32)
    purpose: str = Field(default="publish", min_length=1, max_length=64)
    policy_version: str = Field(min_length=1, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    environment_ref: str = Field(min_length=1, max_length=128)
    assessment_status: PublicationQualificationStatus
    evidence_refs: list[str] = Field(default_factory=list)
    source_revision_refs: list[str] = Field(default_factory=list)


@router.post("/publication-qualifications")
def create_assessment(
    body: PublicationQualificationAssessmentCreate,
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles("catalog_owner"))],
) -> dict[str, object]:
    assessment = append_publication_qualification_assessment(
        db,
        catalog_revision_id=body.catalog_revision_id,
        channel=body.channel,
        purpose=body.purpose,
        policy_version=body.policy_version,
        adapter_version=body.adapter_version,
        environment_ref=body.environment_ref,
        assessment_status=body.assessment_status,
        evidence_refs=body.evidence_refs,
        source_revision_refs=body.source_revision_refs,
    )
    return {
        "assessmentId": str(assessment.id),
        "catalogRevisionId": str(assessment.catalog_revision_id),
        "assessmentStatus": assessment.assessment_status.value,
        "sourceFingerprint": assessment.source_fingerprint,
        "assessedAt": assessment.assessed_at.isoformat(),
    }
