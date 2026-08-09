"""Listing publications and external id mappings."""

from __future__ import annotations

import enum

from sqlalchemy import JSON, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class ListingStatus(enum.StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PENDING_APPROVAL = "pending_approval"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    PUBLISH_FAILED = "publish_failed"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class ListingPublication(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "listing_publication"

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="shopify")
    shopify_product_gid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=ListingStatus.DRAFT,
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    remote_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExternalIdMapping(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "external_id_mapping"
    __table_args__ = (
        UniqueConstraint("sku", "channel", name="uq_external_id_mapping_sku_channel"),
    )

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


__all__ = ["ExternalIdMapping", "ListingPublication", "ListingStatus"]
