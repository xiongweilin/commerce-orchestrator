import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


DEMO_SESSIONS_TABLE = "demo_sessions"
TICKETS_TABLE = "tickets"


class DemoSession(Base):
    __tablename__ = "demo_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("session_id", "external_id", name="uq_session_ticket"),
        UniqueConstraint("session_id", "idempotency_key", name="uq_session_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey(f"{DEMO_SESSIONS_TABLE}.id"), nullable=True)  # noqa: E501
    user_type: Mapped[str] = mapped_column(String(32), default="member")
    channel: Mapped[str] = mapped_column(String(32), default="support")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    current_status: Mapped[str] = mapped_column(String(32), default="open")
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="live")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey(f"{TICKETS_TABLE}.id"), unique=True, index=True)  # noqa: E501
    payload: Mapped[dict] = mapped_column(JSON)
    problem_type: Mapped[str] = mapped_column(String(32), index=True)
    product_area: Mapped[str] = mapped_column(String(32), index=True)
    suggested_owner: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    needs_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(32), index=True)
    workflow_version: Mapped[str] = mapped_column(String(64))
    analysis_source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey(f"{TICKETS_TABLE}.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TicketReview(Base):
    __tablename__ = "ticket_reviews"
    __table_args__ = (UniqueConstraint("session_id", "ticket_id", name="uq_session_review"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey(f"{DEMO_SESSIONS_TABLE}.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey(f"{TICKETS_TABLE}.id"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    corrections: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IssueCluster(Base):
    __tablename__ = "issue_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="low")
    trend: Mapped[str] = mapped_column(String(16), default="stable")
    suggested_owner: Mapped[str] = mapped_column(String(32))
    centroid: Mapped[list] = mapped_column(JSON, default=list)
    representative_ticket_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    narrative_source: Mapped[str] = mapped_column(String(32), default="deterministic")
    narrative_workflow_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ClusterMember(Base):
    __tablename__ = "cluster_members"
    __table_args__ = (UniqueConstraint("cluster_id", "ticket_id", name="uq_cluster_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("issue_clusters.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey(f"{TICKETS_TABLE}.id"), index=True)


class SOPCandidate(Base):
    __tablename__ = "sop_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("issue_clusters.id"), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    generation_source: Mapped[str] = mapped_column(String(32), default="deterministic")
    workflow_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SOPReview(Base):
    __tablename__ = "sop_reviews"
    __table_args__ = (UniqueConstraint("session_id", "candidate_id", name="uq_sop_review"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey(f"{DEMO_SESSIONS_TABLE}.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("sop_candidates.id"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    generation_source: Mapped[str] = mapped_column(String(32), default="deterministic")
    workflow_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
