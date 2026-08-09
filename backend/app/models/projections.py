"""Projections: cached materialized views of external-system state."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin


class Projection(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "projection"
    __table_args__ = (
        UniqueConstraint(
            "owner", "source", "external_id", name="uq_projection_owner_source_external_id"
        ),
    )

    owner: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


__all__ = ["Projection"]
