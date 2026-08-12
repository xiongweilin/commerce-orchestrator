"""Runtime process heartbeats (worker / API liveness)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.base import Base


class RuntimeHeartbeat(Base):
    """One row per process instance; heartbeats upsert on
    ``(process_name, instance_id)`` and refresh ``heartbeat_at``.
    """

    __tablename__ = "runtime_heartbeat"
    __table_args__ = (
        UniqueConstraint(
            "process_name",
            "instance_id",
            name="uq_runtime_heartbeat_process_name_instance_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    process_name: Mapped[str] = mapped_column(String(64), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    heartbeat_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


__all__ = ["RuntimeHeartbeat"]
