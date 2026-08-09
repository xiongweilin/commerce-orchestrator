"""Reconciliation runs and diffs between ledger and external systems."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin, VersionMixin, enum_values


class ReconciliationRunStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPLETED_WITH_DIFFS = "completed_with_diffs"


class ReconciliationDiffStatus(enum.StrEnum):
    OPEN = "OPEN"
    MANUAL_RECONCILIATION = "MANUAL_RECONCILIATION"
    RESOLVED = "RESOLVED"


class ReconciliationRun(UUIDPkMixin, VersionMixin, Base):
    __tablename__ = "reconciliation_run"

    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ReconciliationRunStatus] = mapped_column(
        Enum(
            ReconciliationRunStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ReconciliationRunStatus.RUNNING,
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ReconciliationDiff(UUIDPkMixin, VersionMixin, Base):
    __tablename__ = "reconciliation_diff"
    __table_args__ = (Index("ix_reconciliation_diff_status", "status"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reconciliation_run.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    expected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actual: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    difference: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ReconciliationDiffStatus] = mapped_column(
        Enum(
            ReconciliationDiffStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ReconciliationDiffStatus.OPEN,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "ReconciliationDiff",
    "ReconciliationDiffStatus",
    "ReconciliationRun",
    "ReconciliationRunStatus",
]
