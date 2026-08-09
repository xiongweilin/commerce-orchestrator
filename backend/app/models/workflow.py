"""Workflow runs, work items (human tasks) and decisions."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class WorkflowRunStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItemKind(enum.StrEnum):
    APPROVAL = "approval"
    CONFIRMATION = "confirmation"
    MANUAL_STEP = "manual_step"


class WorkItemStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WorkItemDecisionType(enum.StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CONFIRM = "confirm"
    CANCEL = "cancel"


class WorkflowRun(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "workflow_run"
    __table_args__ = (Index("ix_workflow_run_status_updated_at", "status", "updated_at"),)

    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WorkflowRunStatus] = mapped_column(
        Enum(
            WorkflowRunStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=WorkflowRunStatus.ACCEPTED,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkItem(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    """A human task (approval/confirmation/manual step) inside a workflow."""

    __tablename__ = "work_item"
    __table_args__ = (
        Index("ix_work_item_status_expires_at", "status", "expires_at"),
        Index("ix_work_item_workflow_id", "workflow_id"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_run.id"), nullable=False)
    kind: Mapped[WorkItemKind] = mapped_column(
        Enum(WorkItemKind, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    required_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[WorkItemStatus] = mapped_column(
        Enum(WorkItemStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=WorkItemStatus.PENDING,
    )
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkItemDecision(UUIDPkMixin, Base):
    """Append-only record of every decision submitted on a work item."""

    __tablename__ = "work_item_decision"

    work_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_item.id"), nullable=False)
    decision: Mapped[WorkItemDecisionType] = mapped_column(
        Enum(
            WorkItemDecisionType,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


__all__ = [
    "WorkItem",
    "WorkItemDecision",
    "WorkItemDecisionType",
    "WorkItemKind",
    "WorkItemStatus",
    "WorkflowRun",
    "WorkflowRunStatus",
]
