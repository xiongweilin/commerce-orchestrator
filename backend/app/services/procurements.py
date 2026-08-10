"""Procurement order status queries for the operations console."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.models.procurement import ProcurementOrder, ProcurementStatus


def list_procurements(
    db,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of procurement orders (newest first) for the console."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(ProcurementOrder)
    count_stmt = select(func.count()).select_from(ProcurementOrder)
    if status:
        try:
            status_enum = ProcurementStatus(status)
        except ValueError as exc:
            raise ValidationError(f"unknown procurement status: {status}") from exc
        stmt = stmt.where(ProcurementOrder.status == status_enum)
        count_stmt = count_stmt.where(ProcurementOrder.status == status_enum)
    total = db.execute(count_stmt).scalar_one()
    rows = (
        db.execute(stmt.order_by(ProcurementOrder.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "sku": row.sku,
                "qty": str(row.qty),
                "uom": row.uom,
                "supplier": row.supplier,
                "unitCost": str(row.unit_cost),
                "currency": row.currency,
                "status": row.status.value,
                "odooPoId": row.odoo_po_id,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["list_procurements"]
