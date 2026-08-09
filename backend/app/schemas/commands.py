"""Command request/response models for the operations API.

Every ``*Create`` command is accepted asynchronously: endpoints return
:class:`AcceptedResponse` (``workflowId`` + ``statusUrl``). Clients may attach
an ``Idempotency-Key`` header (see ``app.schemas.base.IDEMPOTENCY_KEY_HEADER``)
to make retries safe.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import AcceptedResponse, Money


class CatalogRevisionCreate(BaseModel):
    """Create a draft catalog revision for a SKU."""

    sku: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=128)
    proposed: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_revision: str | None = Field(default=None, max_length=64)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ListingPublicationCreate(BaseModel):
    """Request publication of a SKU on a sales channel."""

    sku: str = Field(min_length=1, max_length=64)
    channel: str = Field(default="shopify", max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class ProcurementCreate(BaseModel):
    """Create a procurement order (demand_detected)."""

    sku: str = Field(min_length=1, max_length=64)
    qty: Money
    uom: str = Field(default="unit", min_length=1, max_length=8)
    supplier: str = Field(min_length=1, max_length=128)
    unit_cost: Money
    currency: str = Field(default="CNY", min_length=3, max_length=3)


class ReturnCreate(BaseModel):
    """Register a customer return case."""

    return_ref: str | None = Field(default=None, max_length=64)
    shopify_order_id: str | None = Field(default=None, max_length=64)
    order_ref: str | None = Field(default=None, max_length=64)
    customer_ref: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=255)


class ReconciliationCreate(BaseModel):
    """Trigger a reconciliation run."""

    run_type: str = Field(min_length=1, max_length=32)
    domains: list[str] = Field(default_factory=lambda: ["effect"], max_length=16)
    scope: dict[str, Any] = Field(default_factory=dict)


class WorkItemDecisionSubmit(BaseModel):
    """Submit a decision on a pending work item."""

    decision: Literal["approve", "reject", "confirm", "cancel"]
    reason: str | None = Field(default=None, max_length=2000)
    expectedWorkflowVersion: int | None = Field(default=None, ge=1)


class StatusUrlResponse(BaseModel):
    """Pollable status URL for an accepted workflow."""

    workflowId: UUID
    statusUrl: str


CreateCommandResponse = AcceptedResponse
"""All create-command endpoints return AcceptedResponse semantics."""


__all__ = [
    "CatalogRevisionCreate",
    "CreateCommandResponse",
    "ListingPublicationCreate",
    "ProcurementCreate",
    "ReconciliationCreate",
    "ReturnCreate",
    "StatusUrlResponse",
    "WorkItemDecisionSubmit",
]
