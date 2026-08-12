"""Procurement orders: PO lifecycle mirrored against Odoo."""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import CHAR, DateTime, Enum, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class ProcurementStatus(enum.StrEnum):
    DEMAND_DETECTED = "demand_detected"
    RFQ_DRAFT = "rfq_draft"
    PENDING_APPROVAL = "pending_approval"
    PO_CONFIRMED = "po_confirmed"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    BILL_POSTED = "bill_posted"
    IN_PAYMENT = "in_payment"
    RECONCILED = "reconciled"
    CLOSED = "closed"


class ProcurementOrder(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "procurement_order"

    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    uom: Mapped[str] = mapped_column(String(8), nullable=False, default="unit")
    supplier: Mapped[str] = mapped_column(String(128), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[ProcurementStatus] = mapped_column(
        Enum(
            ProcurementStatus,
            native_enum=False,
            length=32,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ProcurementStatus.DEMAND_DETECTED,
    )
    odoo_po_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Remote entity id produced by odoo.bill_create (account.move id); the
    # bill lifecycle is only advanced after the effect succeeds.
    odoo_bill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["ProcurementOrder", "ProcurementStatus"]
