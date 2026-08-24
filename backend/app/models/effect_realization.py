"""Append-only assessments of whether an external effect was realized."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin, enum_values


class EffectRealizationStatus(enum.StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EffectRealizationAssessment(UUIDPkMixin, Base):
    """Immutable reality judgment about one effect-ledger entry."""

    __tablename__ = "effect_realization_assessment"

    effect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("effect_ledger_entry.id"), nullable=False, index=True
    )
    realization_status: Mapped[EffectRealizationStatus] = mapped_column(
        Enum(
            EffectRealizationStatus,
            native_enum=False,
            length=16,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assessed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    assessed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = ["EffectRealizationAssessment", "EffectRealizationStatus"]
