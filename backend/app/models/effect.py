"""Effect ledger: durable record of every external side effect."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class EffectStatus(enum.StrEnum):
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RECONCILED = "reconciled"
    MANUAL_RECONCILIATION = "manual_reconciliation"


class EffectLedgerEntry(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    """One row per intended external side effect (system.operation).

    ``operation`` holds the operation name without the system prefix; combine
    with ``target_system`` to form ``EFFECT_OPS`` entries from
    ``app.schemas.events`` (e.g. target_system="shopify", operation="product_publish").
    """

    __tablename__ = "effect_ledger_entry"
    __table_args__ = (
        Index(
            "ix_effect_ledger_entry_target_system_operation",
            "target_system",
            "operation",
        ),
        Index("ix_effect_ledger_entry_status", "status"),
    )

    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    target_system: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[EffectStatus] = mapped_column(
        Enum(EffectStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=EffectStatus.PLANNED,
    )
    remote_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compensation: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["EffectLedgerEntry", "EffectStatus"]
