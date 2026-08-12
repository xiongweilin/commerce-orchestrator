"""GET /v1/sales-orders — sales order list endpoint."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_session, require_roles
from app.services.rbac import DOMAIN_READ_ROLES
from app.services.sales_orders import list_sales_orders

router = APIRouter(prefix="/v1", tags=["sales-orders"])


@router.get("/sales-orders")
def list_orders(
    db: Annotated[Session, Depends(get_session)],
    _user_id: Annotated[uuid.UUID, Depends(get_current_user)],
    _authorized: Annotated[bool, Depends(require_roles(*DOMAIN_READ_ROLES["sales_orders"]))],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List sales orders (整改计划 §四.2 read matrix)."""
    return list_sales_orders(db, status=status, limit=limit, offset=offset)
