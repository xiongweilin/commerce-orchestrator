"""Commerce responsibility-plane persistence projections.

These tables persist Commerce specializations and portable-runtime semantic
records in the Commerce PostgreSQL database. They do not replace DBOS, Odoo,
Shopify or existing domain aggregate ownership.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin


class ResponsibilityObligationStatus(enum.StrEnum):
    OPEN = "open"
    DISCHARGED = "discharged"


class ResponsibilityRecord(Base):
    __tablename__ = "responsibility_record"

    id: Mapped[str] = mapped_column(String(192), primary_key=True)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResponsibilityKnowledgeProjection(Base):
    __tablename__ = "responsibility_knowledge_projection"

    id: Mapped[str] = mapped_column(String(192), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ResponsibilityEvent(Base):
    __tablename__ = "responsibility_event"

    id: Mapped[str] = mapped_column(String(192), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    subject_ref: Mapped[str | None] = mapped_column(String(192), nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResponsibilityBinding(UUIDPkMixin, Base):
    """Commerce subject -> judgment/historical-use provenance sidecar."""

    __tablename__ = "responsibility_binding"

    subject_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_ref: Mapped[str] = mapped_column(String(192), nullable=False, index=True)
    subject_version: Mapped[str] = mapped_column(String(192), nullable=False)
    judgment_ref: Mapped[str] = mapped_column(String(192), nullable=False, index=True)
    historical_use_ref: Mapped[str] = mapped_column(String(192), nullable=False, unique=True)
    requirement_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ExecutionAuthorization(UUIDPkMixin, Base):
    """Explicit Commerce execution authority issued from, but not equal to, a Decision."""

    __tablename__ = "execution_authorization"

    authorization_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    decision_ref: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("work_item_decision.id"), nullable=False, index=True
    )
    workflow_ref: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_run.id"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(192), nullable=False, index=True)
    subject_version: Mapped[str | None] = mapped_column(String(192), nullable=True)
    subject_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_system: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_operations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_ref: Mapped[str] = mapped_column(String(192), nullable=False)
    issued_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id"), nullable=False, index=True
    )
    issued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfirmedOutcome(UUIDPkMixin, Base):
    """Business outcome confirmation derived from explicit reality verification."""

    __tablename__ = "confirmed_outcome"

    effect_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("effect_ledger_entry.id"), nullable=False, unique=True, index=True
    )
    realization_assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("effect_realization_assessment.id"),
        nullable=False,
        unique=True,
    )
    outcome_type: Mapped[str] = mapped_column(String(96), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id"), nullable=False, index=True
    )
    confirmed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResponsibilityObligation(UUIDPkMixin, Base):
    """Explicit open responsibility; a mismatch alone never globally invalidates experience."""

    __tablename__ = "responsibility_obligation"

    subject_ref: Mapped[str] = mapped_column(String(192), nullable=False, index=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(192), nullable=False, index=True)
    status: Mapped[ResponsibilityObligationStatus] = mapped_column(
        String(16), nullable=False, default=ResponsibilityObligationStatus.OPEN
    )
    reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    projection_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    discharged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("user.id"), nullable=False, index=True
    )


__all__ = [
    "ConfirmedOutcome",
    "ExecutionAuthorization",
    "ResponsibilityBinding",
    "ResponsibilityEvent",
    "ResponsibilityKnowledgeProjection",
    "ResponsibilityObligation",
    "ResponsibilityObligationStatus",
    "ResponsibilityRecord",
]
