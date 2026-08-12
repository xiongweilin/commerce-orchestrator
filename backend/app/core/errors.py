"""Domain exception hierarchy and RFC 7807 problem-detail handlers."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class CommerceError(Exception):
    """Base class for every domain error surfaced to the API."""

    status_code: int = 500
    type: str = "about:blank"
    title: str = "Internal error"

    def __init__(self, detail: str, *, instance: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.instance = instance

    def to_problem(self) -> dict[str, Any]:
        """Return the RFC 7807 problem-detail representation."""
        return {
            "type": self.type,
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": self.instance,
        }


class NotFoundError(CommerceError):
    status_code = 404
    type = "not-found"
    title = "Resource not found"


class ConflictError(CommerceError):
    status_code = 409
    type = "conflict"
    title = "Conflict"


class ValidationError(CommerceError):
    status_code = 422
    type = "validation-error"
    title = "Validation error"


class PermissionDeniedError(CommerceError):
    status_code = 403
    type = "permission-denied"
    title = "Permission denied"


class IdempotencyConflictError(ConflictError):
    type = "idempotency-conflict"
    title = "Idempotency conflict"


class VersionConflictError(ConflictError):
    type = "version-conflict"
    title = "Optimistic lock conflict"


class ExternalSystemError(CommerceError):
    status_code = 502
    type = "external-system-error"
    title = "External system error"


class RetryableEffectError(ExternalSystemError):
    """Definitive remote failure that is safe to retry (effect not applied).

    Raised when the external system definitively did **not** apply the effect
    but the failure is transient and retrying the same intent is safe (e.g.
    HTTP 429 rate limiting received before the request was processed). Maps
    to ``EffectFailed(retryable=True)``; the effect ledger bounds retries to
    3 attempts, after which the effect escalates to manual reconciliation.

    Distinct from :class:`OutcomeUnknownError`, which signals an **ambiguous**
    remote outcome and must never be blind-retried.
    """

    type = "retryable-effect-error"
    title = "Retryable effect failure"


async def commerce_error_handler(request: Request, exc: CommerceError) -> JSONResponse:
    """Serialize a domain error as an RFC 7807 problem detail."""
    problem = exc.to_problem()
    if problem["instance"] is None:
        problem["instance"] = request.url.path
    return JSONResponse(status_code=exc.status_code, content=problem)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Serialize FastAPI request-validation failures as problem details."""
    detail = "; ".join(f"{list(e.get('loc', []))}: {e.get('msg')}" for e in exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "type": "validation-error",
            "title": "Request validation error",
            "status": 422,
            "detail": detail,
            "instance": request.url.path,
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: log the real error, never leak its details."""
    logger.exception("unhandled exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank",
            "title": "Internal server error",
            "status": 500,
            "detail": "An unexpected error occurred",
            "instance": request.url.path,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the RFC 7807 handlers to a FastAPI application."""
    app.add_exception_handler(CommerceError, commerce_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)


__all__ = [
    "CommerceError",
    "ConflictError",
    "ExternalSystemError",
    "IdempotencyConflictError",
    "NotFoundError",
    "PermissionDeniedError",
    "RetryableEffectError",
    "ValidationError",
    "VersionConflictError",
    "commerce_error_handler",
    "register_exception_handlers",
    "unhandled_error_handler",
    "validation_error_handler",
]
