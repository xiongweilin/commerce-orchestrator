"""GET /v1/sales-orders — sales order list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session
from app.services.sales_orders import list_sales_orders

router = APIRouter(prefix="/v1", tags=["sales-orders"])


@router.get("/sales-orders")
def list_orders(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List sales orders (operations console domain view)."""
    return list_sales_orders(db, status=status, limit=limit, offset=offset)
