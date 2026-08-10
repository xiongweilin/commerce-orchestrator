"""Return case status queries for the operations console."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.models.returns import ReturnCase, ReturnStatus


def list_return_cases(
    db,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a page of return cases (newest first) for the console."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    stmt = select(ReturnCase)
    count_stmt = select(func.count()).select_from(ReturnCase)
    if status:
        try:
            status_enum = ReturnStatus(status)
        except ValueError as exc:
            raise ValidationError(f"unknown return case status: {status}") from exc
        stmt = stmt.where(ReturnCase.status == status_enum)
        count_stmt = count_stmt.where(ReturnCase.status == status_enum)
    total = db.execute(count_stmt).scalar_one()
    cases = (
        db.execute(stmt.order_by(ReturnCase.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "returnRef": case.return_ref,
                "shopifyOrderId": case.shopify_order_id,
                "orderRef": case.order_ref,
                "reason": case.reason,
                "status": case.status.value,
                "refundAmount": (
                    str(case.refund_amount) if case.refund_amount is not None else None
                ),
                "currency": case.currency,
                "disposition": case.disposition.value if case.disposition else None,
                "creditNoteId": case.credit_note_id,
                "shopifyRefundGid": case.shopify_refund_gid,
                "createdAt": case.created_at.isoformat(),
            }
            for case in cases
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["list_return_cases"]
