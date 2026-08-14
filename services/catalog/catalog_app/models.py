import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CatalogRun(Base):
    __tablename__ = "catalog_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="processing", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    items: Mapped[list["CatalogItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_runs.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(String(32), index=True)
    source_title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[str] = mapped_column(Text, default="")
    listing_title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    selling_points: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="imported", index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    draft_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    workflow_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    draft_attempts: Mapped[int] = mapped_column(Integer, default=0)
    erp_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    erp_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    run: Mapped[CatalogRun] = relationship(back_populates="items")


class ERPOperation(Base):
    __tablename__ = "erp_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    operation_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id", ondelete="CASCADE"))
    result: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
