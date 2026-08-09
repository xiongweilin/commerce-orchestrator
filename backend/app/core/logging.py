"""Structured logging: structlog config, correlation ids, request middleware."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import get_settings
from app.core.uuid7 import uuid7

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_configured = False


def get_correlation_id() -> str | None:
    """Return the correlation id bound to the current context."""
    return _correlation_id.get()


def bind_correlation_id(correlation_id: str | None = None) -> str:
    """Bind a correlation id to the current async/sync context.

    Generates a UUIDv7 when none is given and returns the effective value.
    """
    if correlation_id is None:
        correlation_id = str(uuid7())
    _correlation_id.set(correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    return correlation_id


def clear_context() -> None:
    """Reset the correlation id and all structlog context vars."""
    _correlation_id.set(None)
    structlog.contextvars.clear_contextvars()


def configure_logging() -> None:
    """Configure structlog: JSON lines in prod, dev console otherwise."""
    global _configured
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)
    shared_processors: list[object] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.environment == "dev":
        processors = [*shared_processors, structlog.dev.ConsoleRenderer()]
    else:
        processors = [*shared_processors, structlog.processors.JSONRenderer()]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, configuring logging on first use."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a correlation id to every request and log start/finish.

    Uses ``X-Correlation-Id`` or ``X-Request-Id`` when present, otherwise
    generates a UUIDv7. Add to the app with
    ``app.add_middleware(RequestContextMiddleware)``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = (
            request.headers.get("x-correlation-id")
            or request.headers.get("x-request-id")
            or str(uuid7())
        )
        token = _correlation_id.set(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        logger = structlog.get_logger("commerce.http")
        try:
            logger.info("http_request_started", method=request.method, path=request.url.path)
            response = await call_next(request)
        except Exception:
            logger.exception("http_request_failed", method=request.method, path=request.url.path)
            raise
        else:
            logger.info(
                "http_request_finished",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
            _correlation_id.reset(token)


__all__ = [
    "RequestContextMiddleware",
    "bind_correlation_id",
    "clear_context",
    "configure_logging",
    "get_correlation_id",
    "get_logger",
]
