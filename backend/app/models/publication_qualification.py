"""Append-only, context-bound publication qualification assessments."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin, enum_values


class PublicationQualificationStatus(enum.StrEnum):
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    REVALIDATION_REQUIRED = "revalidation_required"


class PublicationQualificationAssessment(UUIDPkMixin, Base):
    """Immutable assessment for one exact publication context."""

    __tablename__ = "publication_qualification_assessment"

    catalog_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_revision.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    assessment_status: Mapped[PublicationQualificationStatus] = mapped_column(
        Enum(
            PublicationQualificationStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assessed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id"), nullable=True, index=True
    )
    assessed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = [
    "PublicationQualificationAssessment",
    "PublicationQualificationStatus",
]
