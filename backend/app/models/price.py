"""Pricing offers and approvals."""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import CHAR, Boolean, Enum, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class PriceOfferStatus(enum.StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class PriceOffer(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "price_offer"

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    margin_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[PriceOfferStatus] = mapped_column(
        Enum(PriceOfferStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=PriceOfferStatus.DRAFT,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


__all__ = ["PriceOffer", "PriceOfferStatus"]
