"""Sensitive payload vault: encrypted blobs with retention metadata."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, UUIDPkMixin


class SensitivePayload(UUIDPkMixin, Base):
    """A retained encrypted payload (raw webhook, shipping/customer data).

    ``ciphertext`` never holds plaintext; the worker-side privacy service
    (WP4) writes the encrypted blob and runs the retention cleanup job.
    """

    __tablename__ = "sensitive_payload"
    __table_args__ = (
        # Retention cleanup scans by expiry.
        Index("ix_sensitive_payload_expires_at", "expires_at"),
    )

    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = ["SensitivePayload"]
