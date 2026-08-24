"""Append-only reconciliation disposition and verification records."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin, enum_values


class ReconciliationResolutionKind(enum.StrEnum):
    REPAIRED = "repaired"
    ACCEPTED_DIFFERENCE = "accepted_difference"
    AUTHORITATIVE_EXTERNAL = "authoritative_external"
    AUTHORITATIVE_INTERNAL = "authoritative_internal"
    SUPERSEDED = "superseded"
    FALSE_POSITIVE = "false_positive"


class ReconciliationVerificationStatus(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReconciliationResolution(UUIDPkMixin, Base):
    """Immutable disposition plus an independent verification judgment."""

    __tablename__ = "reconciliation_resolution"

    reconciliation_diff_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_diff.id"), nullable=False, index=True
    )
    resolution_kind: Mapped[ReconciliationResolutionKind] = mapped_column(
        Enum(
            ReconciliationResolutionKind,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    verification_status: Mapped[ReconciliationVerificationStatus] = mapped_column(
        Enum(
            ReconciliationVerificationStatus,
            native_enum=False,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    basis_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = [
    "ReconciliationResolution",
    "ReconciliationResolutionKind",
    "ReconciliationVerificationStatus",
]
