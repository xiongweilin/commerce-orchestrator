"""Shared schema primitives: money, RFC 3339 timestamps, problem details, paging."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
"""Header that carries the client idempotency key on write commands.

Clients must send the same ``Idempotency-Key`` for retries of the same
request; the server deduplicates on ``(scope, key)`` via the
``idempotency_record`` table and returns the stored result.
"""


def _money_serializer(value: Decimal) -> str:
    return str(value)


def _rfc3339_serializer(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


Money = Annotated[
    Decimal,
    PlainSerializer(_money_serializer, return_type=str, when_used="json"),
]
"""Decimal money value serialized as a string in JSON."""

Rfc3339Datetime = Annotated[
    datetime,
    PlainSerializer(_rfc3339_serializer, return_type=str, when_used="json"),
]
"""Aware datetime serialized as an RFC 3339 string in JSON."""


class ErrorBody(BaseModel):
    """RFC 7807 problem detail body returned by all error handlers."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str | None = None


class AcceptedResponse(BaseModel):
    """Response for commands that are accepted asynchronously."""

    workflowId: UUID
    status: Literal["accepted"] = "accepted"
    statusUrl: str


class Page[T](BaseModel):
    """Offset-paged envelope returned by list endpoints."""

    items: list[T]
    total: int
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


__all__ = [
    "AcceptedResponse",
    "ErrorBody",
    "IDEMPOTENCY_KEY_HEADER",
    "Money",
    "Page",
    "Rfc3339Datetime",
]
