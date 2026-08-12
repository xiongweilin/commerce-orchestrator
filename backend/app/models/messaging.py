"""Messaging: idempotency records, outbox and inbox event queues."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.base import Base, enum_values


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"


class InboxStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_record_scope_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Vocabulary fixed to processing | completed (legacy 'pending'/'done'
    # values are normalized by migration 0004).
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (Index("ix_outbox_event_status_next_attempt_at", "status", "next_attempt_at"),)

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    producer: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class InboxEvent(Base):
    __tablename__ = "inbox_event"
    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_inbox_event_consumer_event_id"),
        # Worker claim scan: consumer + actionable + retry/backoff ordering.
        Index(
            "ix_inbox_event_consumer_status_next_attempt_at",
            "consumer",
            "status",
            "next_attempt_at",
        ),
        # Generic claim/backoff scan across consumers.
        Index("ix_inbox_event_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    status: Mapped[InboxStatus] = mapped_column(
        Enum(InboxStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=InboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["IdempotencyRecord", "InboxEvent", "InboxStatus", "OutboxEvent", "OutboxStatus"]
