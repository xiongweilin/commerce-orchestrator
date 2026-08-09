"""Feedback intake, sanitization and clustering."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values
from app.schemas.events import FEEDBACK_TYPES


class FeedbackType(enum.StrEnum):
    """Fixed feedback taxonomy; values mirror ``app.schemas.events.FEEDBACK_TYPES``."""

    PRODUCT_QUALITY = "product_quality"
    CONTENT_ACCURACY = "content_accuracy"
    PRICING_PROMOTION = "pricing_promotion"
    AVAILABILITY = "availability"
    PAYMENT = "payment"
    FULFILLMENT = "fulfillment"
    PACKAGING = "packaging"
    SERVICE = "service"
    RETURN_REFUND = "return_refund"
    FRAUD_ABUSE = "fraud_abuse"
    OTHER = "other"


assert {t.value for t in FeedbackType} == set(FEEDBACK_TYPES), (
    "FeedbackType drifted from app.schemas.events.FEEDBACK_TYPES"
)


class FeedbackStatus(enum.StrEnum):
    OBSERVED = "observed"
    CLUSTERED = "clustered"
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    PROMOTED_TO_SOP = "promoted_to_sop"
    PROMOTED_TO_CATALOG_CHANGE = "promoted_to_catalog_change"
    REJECTED = "rejected"


class FeedbackCluster(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "feedback_cluster"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    items_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class FeedbackItem(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "feedback_item"

    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[FeedbackType] = mapped_column(
        Enum(FeedbackType, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
    )
    sanitized_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=FeedbackStatus.OBSERVED,
    )
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("feedback_cluster.id"), nullable=True
    )
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["FeedbackCluster", "FeedbackItem", "FeedbackStatus", "FeedbackType"]
