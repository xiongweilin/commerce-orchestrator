"""FastAPI application factory for the operations control tower API.

Assembles the request context middleware, local-dev CORS, the contract error
envelope handlers, health/metrics endpoints and the version-1 routers. DBOS is
only bootstrapped lazily (and non-fatally) at startup — the blocking worker
runtime belongs to ``app.worker``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import v1
from app.api.deps import UnauthenticatedError
from app.config import get_settings
from app.core.errors import (
    CommerceError,
    ConflictError,
    ExternalSystemError,
    IdempotencyConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.logging import (
    RequestContextMiddleware,
    configure_logging,
    get_correlation_id,
    get_logger,
)
from app.core.telemetry import METRICS_REGISTRY, setup_telemetry
from app.core.uuid7 import uuid7

logger = get_logger("commerce.api")


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Build the single contract error envelope."""
    return {
        "error": {
            "code": code,
            "message": message,
            "correlationId": get_correlation_id() or str(uuid7()),
            "details": details,
        }
    }


def _map_commerce_error(exc: CommerceError) -> tuple[int, str]:
    """Map a domain exception to the contract (HTTP status, error code) pair."""
    if isinstance(exc, UnauthenticatedError):
        return 401, "unauthenticated"
    if isinstance(exc, IdempotencyConflictError):
        return 409, "idempotency_key_conflict"
    if isinstance(exc, VersionConflictError):
        return 409, "workflow_version_conflict"
    if isinstance(exc, NotFoundError):
        return 404, "not_found"
    if isinstance(exc, PermissionDeniedError):
        return 403, "permission_denied"
    if isinstance(exc, ValidationError):
        return 422, "validation_error"
    if isinstance(exc, ExternalSystemError):
        return 502, "external_system_error"
    if isinstance(exc, ConflictError):
        return 409, "state_conflict"
    return 500, "internal_error"


async def _commerce_error_handler(request: Request, exc: CommerceError) -> JSONResponse:
    status_code, code = _map_commerce_error(exc)
    message = exc.detail or exc.title
    return JSONResponse(status_code=status_code, content=_error_body(code, message))


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
        fields.append({"field": loc or "body", "message": err.get("msg", "invalid")})
    return JSONResponse(
        status_code=422,
        content=_error_body("validation_error", "Request validation failed", {"fields": fields}),
    )


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        400: "validation_error",
        401: "unauthenticated",
        403: "permission_denied",
        404: "not_found",
        409: "state_conflict",
        422: "validation_error",
    }.get(exc.status_code, "internal_error")
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail)),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "An unexpected error occurred"),
    )


def _register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that always emit the contract error envelope."""
    app.add_exception_handler(CommerceError, _commerce_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks. DBOS bootstrap is lazy and non-fatal here."""
    configure_logging()
    setup_telemetry()
    try:
        from app.workflows.bootstrap import configure_dbos

        configure_dbos()
    except Exception:
        logger.warning("dbos_bootstrap_skipped", exc_info=True)
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(
        title="commerce-orchestrator",
        version="0.1.0",
        description="E-commerce internal operations control tower command API",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    if get_settings().environment == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    _register_error_handlers(app)
    app.include_router(v1.commands.router)
    app.include_router(v1.decisions.router)
    app.include_router(v1.procurements.router)
    app.include_router(v1.reconciliations.router)
    app.include_router(v1.return_cases.router)
    app.include_router(v1.sales_orders.router)
    app.include_router(v1.workflows.router)
    app.include_router(v1.webhooks.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe; compose healthcheck depends on the 2xx status."""
        return {"status": "ok"}

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        """Prometheus exposition of the shared metrics registry."""
        return Response(
            content=generate_latest(METRICS_REGISTRY),
            media_type="text/plain; version=0.0.4",
        )

    return app


app = create_app()
