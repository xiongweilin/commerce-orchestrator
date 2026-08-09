"""Return cases: from customer request to refund reconciliation."""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import CHAR, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class ReturnStatus(enum.StrEnum):
    REQUESTED = "requested"
    ELIGIBILITY_REVIEW = "eligibility_review"
    AUTHORIZED = "authorized"
    RECEIVED = "received"
    INSPECTED = "inspected"
    DISPOSITION_APPROVED = "disposition_approved"
    CREDIT_NOTE_POSTED = "credit_note_posted"
    REFUND_PENDING = "refund_pending"
    REFUND_SUCCEEDED = "refund_succeeded"
    RECONCILED = "reconciled"
    CLOSED = "closed"


class ReturnDisposition(enum.StrEnum):
    RESTOCK = "restock"
    REPAIR = "repair"
    SCRAP = "scrap"


class ReturnCase(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "return_case"

    return_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    shopify_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=ReturnStatus.REQUESTED,
    )
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    disposition: Mapped[ReturnDisposition | None] = mapped_column(
        Enum(
            ReturnDisposition,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=True,
    )
    odoo_return_move_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credit_note_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shopify_refund_gid: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = ["ReturnCase", "ReturnDisposition", "ReturnStatus"]
