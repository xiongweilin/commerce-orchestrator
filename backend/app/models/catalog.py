"""Catalog change candidates and revisions."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class CatalogCandidateStatus(enum.StrEnum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    FROZEN = "frozen"
    SCORED = "scored"
    OFFICIAL = "official"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class CatalogRevisionStatus(enum.StrEnum):
    DRAFT = "draft"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    OFFICIAL = "official"
    SUPERSEDED = "superseded"


class CatalogChangeCandidate(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    """A candidate catalog change derived from feedback/evidence."""

    __tablename__ = "catalog_change_candidate"

    source_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sanitizer_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    proposal_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposal_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[CatalogCandidateStatus] = mapped_column(
        Enum(
            CatalogCandidateStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=CatalogCandidateStatus.DRAFT,
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogRevision(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    """A concrete revision of a SKU's catalog attributes."""

    __tablename__ = "catalog_revision"

    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_change_candidate.id"), nullable=True
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[CatalogRevisionStatus] = mapped_column(
        Enum(
            CatalogRevisionStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=CatalogRevisionStatus.DRAFT,
    )
    current: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "CatalogCandidateStatus",
    "CatalogChangeCandidate",
    "CatalogRevision",
    "CatalogRevisionStatus",
]
