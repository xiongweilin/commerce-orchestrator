"""Sales order status queries for the operations console."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.models.order import SalesOrder, SalesOrderStatus


def list_sales_orders(
    db,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of sales orders (newest first) for the console."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(SalesOrder)
    count_stmt = select(func.count()).select_from(SalesOrder)
    if status:
        try:
            status_enum = SalesOrderStatus(status)
        except ValueError as exc:
            raise ValidationError(f"unknown sales order status: {status}") from exc
        stmt = stmt.where(SalesOrder.status == status_enum)
        count_stmt = count_stmt.where(SalesOrder.status == status_enum)
    total = db.execute(count_stmt).scalar_one()
    orders = (
        db.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "workflowId": None,
                "orderRef": order.order_ref,
                "shopifyOrderId": order.shopify_order_id,
                "customerRef": order.customer_ref,
                "status": order.status.value,
                "currency": order.currency,
                "total": str(order.total),
                "createdAt": order.created_at.isoformat(),
                "updatedAt": order.updated_at.isoformat(),
            }
            for order in orders
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["list_sales_orders"]
