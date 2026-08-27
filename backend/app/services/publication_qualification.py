"""Current-use publication qualification without rewriting catalog lifecycle."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable

from sqlalchemy import select

from app.core.errors import NotFoundError, ValidationError
from app.models.catalog import CatalogRevision
from app.models.publication_qualification import (
    PublicationQualificationAssessment,
    PublicationQualificationStatus,
)


def catalog_revision_fingerprint(revision: CatalogRevision) -> str:
    """Fingerprint publication-relevant source content, independent of lifecycle status."""
    payload = {
        "id": str(revision.id),
        "sku": revision.sku,
        "title": revision.title,
        "description": revision.description,
        "category": revision.category,
        "current": revision.current,
        "proposed": revision.proposed,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError(f"{field} is required for publication qualification")
    return text


def append_publication_qualification_assessment(
    db,
    *,
    catalog_revision_id: uuid.UUID,
    channel: str,
    purpose: str,
    policy_version: str,
    adapter_version: str,
    environment_ref: str,
    assessment_status: PublicationQualificationStatus | str,
    assessed_by_user_id: uuid.UUID | None = None,
    evidence_refs: Iterable[str] = (),
    source_revision_refs: Iterable[str] = (),
) -> PublicationQualificationAssessment:
    """Append one assessment. No existing assessment is updated or backfilled."""
    revision = db.get(CatalogRevision, catalog_revision_id)
    if revision is None:
        raise NotFoundError("catalog revision not found")
    status = PublicationQualificationStatus(assessment_status)
    evidence = [str(ref).strip() for ref in evidence_refs if str(ref).strip()]
    if status is PublicationQualificationStatus.QUALIFIED and not evidence:
        raise ValidationError("qualified publication assessment requires evidence_refs")
    refs = [str(ref).strip() for ref in source_revision_refs if str(ref).strip()]
    if not refs:
        refs = [f"catalog_revision:{revision.id}"]
    assessment = PublicationQualificationAssessment(
        catalog_revision_id=revision.id,
        channel=_required_text(channel, "channel"),
        purpose=_required_text(purpose, "purpose"),
        source_revision_refs=refs,
        source_fingerprint=catalog_revision_fingerprint(revision),
        policy_version=_required_text(policy_version, "policy_version"),
        adapter_version=_required_text(adapter_version, "adapter_version"),
        environment_ref=_required_text(environment_ref, "environment_ref"),
        assessment_status=status,
        evidence_refs=evidence,
        assessed_by_user_id=assessed_by_user_id,
    )
    db.add(assessment)
    db.flush()
    return assessment


def latest_applicable_assessment(
    db,
    *,
    revision: CatalogRevision,
    channel: str,
    purpose: str,
    policy_version: str,
    adapter_version: str,
    environment_ref: str,
) -> PublicationQualificationAssessment | None:
    """Latest assessment bound to the exact current source and publication context."""
    return (
        db.execute(
            select(PublicationQualificationAssessment)
            .where(
                PublicationQualificationAssessment.catalog_revision_id == revision.id,
                PublicationQualificationAssessment.channel == channel,
                PublicationQualificationAssessment.purpose == purpose,
                PublicationQualificationAssessment.source_fingerprint
                == catalog_revision_fingerprint(revision),
                PublicationQualificationAssessment.policy_version == policy_version,
                PublicationQualificationAssessment.adapter_version == adapter_version,
                PublicationQualificationAssessment.environment_ref == environment_ref,
            )
            .order_by(
                PublicationQualificationAssessment.assessed_at.desc(),
                PublicationQualificationAssessment.id.desc(),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )


def require_current_publication_qualification(
    db,
    *,
    catalog_revision_id: uuid.UUID,
    expected_sku: str,
    channel: str,
    purpose: str,
    policy_version: str,
    adapter_version: str,
    environment_ref: str,
) -> PublicationQualificationAssessment:
    """Fail closed unless the latest exact-context assessment is QUALIFIED."""
    revision = db.get(CatalogRevision, catalog_revision_id)
    if revision is None:
        raise ValidationError("publication requires a known catalog revision")
    if revision.sku != expected_sku:
        raise ValidationError("publication qualification revision does not match listing SKU")
    assessment = latest_applicable_assessment(
        db,
        revision=revision,
        channel=_required_text(channel, "channel"),
        purpose=_required_text(purpose, "purpose"),
        policy_version=_required_text(policy_version, "policy_version"),
        adapter_version=_required_text(adapter_version, "adapter_version"),
        environment_ref=_required_text(environment_ref, "environment_ref"),
    )
    if assessment is None:
        raise ValidationError("current publication qualification assessment is required")
    if assessment.assessment_status is not PublicationQualificationStatus.QUALIFIED:
        raise ValidationError(
            "latest publication qualification assessment is not qualified: "
            f"{assessment.assessment_status.value}"
        )
    return assessment


__all__ = [
    "append_publication_qualification_assessment",
    "catalog_revision_fingerprint",
    "latest_applicable_assessment",
    "require_current_publication_qualification",
]
