"""Sales orders mirrored from Shopify into Odoo."""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import CHAR, JSON, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin, enum_values


class SalesOrderStatus(enum.StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    ACCEPTED = "accepted"
    ODO_DRAFTED = "odo_drafted"
    CONFIRMED = "confirmed"
    RESERVED = "reserved"
    PICKING = "picking"
    SHIPPED = "shipped"
    INVOICED = "invoiced"
    IN_PAYMENT = "in_payment"
    RECONCILED = "reconciled"
    CLOSED = "closed"


class SalesOrder(UUIDPkMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "sales_order"

    order_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    shopify_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[SalesOrderStatus] = mapped_column(
        Enum(SalesOrderStatus, native_enum=False, length=32, values_callable=enum_values),
        nullable=False,
        default=SalesOrderStatus.RECEIVED,
    )
    odoo_sale_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Remote entity ids for the O2C effect chain: created by
    # odoo.picking_create / odoo.invoice_create and consumed by the matching
    # validate effects (stock.picking.id / account.move.id respectively).
    odoo_picking_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    odoo_invoice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    shipping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fulfillment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)


__all__ = ["SalesOrder", "SalesOrderStatus"]
