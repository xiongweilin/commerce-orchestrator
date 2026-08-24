from __future__ import annotations

# Final non-harness M1 full-suite CI gate anchor.
import uuid
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.publication_qualification import PublicationQualificationStatus
from app.services.publication_qualification import (
    append_publication_qualification_assessment,
    catalog_revision_fingerprint,
    require_current_publication_qualification,
)


def _revision(db, *, sku: str = "SKU-M1") -> CatalogRevision:
    revision = CatalogRevision(
        sku=sku,
        title="M1",
        status=CatalogRevisionStatus.OFFICIAL,
        current={"title": "old"},
        proposed={"title": "new"},
    )
    db.add(revision)
    db.flush()
    return revision


def _append(db, revision, status=PublicationQualificationStatus.QUALIFIED, **overrides):
    values = {
        "catalog_revision_id": revision.id,
        "channel": "shopify",
        "purpose": "publish",
        "policy_version": "policy-v1",
        "adapter_version": "shopify-v1",
        "environment_ref": "prod",
        "assessment_status": status,
        "evidence_refs": ["evidence:qualification:1"]
        if status is PublicationQualificationStatus.QUALIFIED
        else [],
    }
    values.update(overrides)
    return append_publication_qualification_assessment(db, **values)


def _require(db, revision, **overrides):
    values = {
        "catalog_revision_id": revision.id,
        "expected_sku": revision.sku,
        "channel": "shopify",
        "purpose": "publish",
        "policy_version": "policy-v1",
        "adapter_version": "shopify-v1",
        "environment_ref": "prod",
    }
    values.update(overrides)
    return require_current_publication_qualification(db, **values)


def test_official_revision_does_not_imply_current_qualification(db) -> None:
    revision = _revision(db)
    with pytest.raises(
        ValidationError, match="current publication qualification assessment is required"
    ):
        _require(db, revision)


def test_assessments_are_append_only_and_latest_exact_context_governs(db) -> None:
    revision = _revision(db)
    first = _append(db, revision)
    second = _append(db, revision, PublicationQualificationStatus.REVALIDATION_REQUIRED)
    assert first.id != second.id
    assert first.assessment_status is PublicationQualificationStatus.QUALIFIED
    with pytest.raises(ValidationError, match="revalidation_required"):
        _require(db, revision)
    assert (
        db.get(type(first), first.id).assessment_status is PublicationQualificationStatus.QUALIFIED
    )


def test_gate_binds_policy_adapter_environment_channel_and_purpose(db) -> None:
    revision = _revision(db)
    _append(db, revision)
    for mismatch in (
        {"policy_version": "policy-v2"},
        {"adapter_version": "shopify-v2"},
        {"environment_ref": "staging"},
        {"channel": "amazon"},
        {"purpose": "preview"},
    ):
        with pytest.raises(
            ValidationError, match="current publication qualification assessment is required"
        ):
            _require(db, revision, **mismatch)


def test_source_change_requires_revalidation_without_rewriting_old_assessment(db) -> None:
    revision = _revision(db)
    assessment = _append(db, revision)
    before = assessment.source_fingerprint
    revision.proposed = {"title": "changed-after-assessment"}
    db.flush()
    assert catalog_revision_fingerprint(revision) != before
    with pytest.raises(
        ValidationError, match="current publication qualification assessment is required"
    ):
        _require(db, revision)
    assert assessment.source_fingerprint == before


def test_qualified_assessment_requires_durable_evidence_ref(db) -> None:
    revision = _revision(db)
    with pytest.raises(ValidationError, match="requires evidence_refs"):
        _append(db, revision, evidence_refs=[])


def test_publication_seams_do_not_gate_on_official_lifecycle() -> None:
    source = Path("app/services/commands.py").read_text(encoding="utf-8")
    assert source.count("require_current_publication_qualification(") >= 2
    assert (
        "CatalogRevisionStatus.OFFICIAL"
        not in source[
            source.index("def _approve_catalog_revision") : source.index("def _approve_procurement")
        ]
    )


def test_model_has_no_mutable_currently_qualified_flag() -> None:
    source = Path("app/models/catalog.py").read_text(encoding="utf-8")
    assert "currently_qualified" not in source
    assessment_source = Path("app/models/publication_qualification.py").read_text(encoding="utf-8")
    assert "updated_at" not in assessment_source
    assert "VersionMixin" not in assessment_source
