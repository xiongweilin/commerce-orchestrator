"""FastAPI application factory for the operations control tower API.

Assembles the request context middleware, local-dev CORS, the contract error
envelope handlers, health/metrics endpoints and the version-1 routers. DBOS is
only bootstrapped lazily (and non-fatally) at startup — the blocking worker
runtime belongs to ``app.worker``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import generate_latest
from sqlalchemy import select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import v1
from app.api.deps import UnauthenticatedError
from app.config import get_settings
from app.core.db import SessionLocal
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
from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.runtime import RuntimeHeartbeat
from app.schemas.base import (
    ERROR_IDEMPOTENCY_IN_PROGRESS,
    IDEMPOTENCY_RETRY_AFTER_SECONDS,
    RETRY_AFTER_HEADER,
)
from app.services.workflows import IdempotencyInProgressError

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
    if isinstance(exc, IdempotencyInProgressError):
        return 409, ERROR_IDEMPOTENCY_IN_PROGRESS
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


async def _idempotency_in_progress_handler(
    request: Request, exc: IdempotencyInProgressError
) -> JSONResponse:
    """409 ``idempotency_in_progress`` with ``Retry-After: 1``."""
    message = exc.detail or exc.title
    return JSONResponse(
        status_code=409,
        headers={RETRY_AFTER_HEADER: IDEMPOTENCY_RETRY_AFTER_SECONDS},
        content=_error_body(ERROR_IDEMPOTENCY_IN_PROGRESS, message),
    )


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
    app.add_exception_handler(IdempotencyInProgressError, _idempotency_in_progress_handler)
    app.add_exception_handler(CommerceError, _commerce_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)


def _check_result(status: str, message: str | None = None) -> dict[str, str]:
    result: dict[str, str] = {"status": status}
    if message:
        result["message"] = message
    return result


def _check_database() -> dict[str, str]:
    """Readiness: the configured database answers ``SELECT 1``."""
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return _check_result("ok")
    except Exception as exc:  # noqa: BLE001 - probe endpoints must not crash
        return _check_result("fail", f"database unreachable: {type(exc).__name__}")


def _check_alembic() -> dict[str, str]:
    """Readiness: ``alembic_version`` current revision equals the head."""
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        backend_dir = Path(__file__).resolve().parents[1]
        cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with SessionLocal() as session:
            row = session.execute(text("SELECT version_num FROM alembic_version")).first()
        current = row[0] if row else None
        if current == head:
            return _check_result("ok", f"alembic head {head}")
        return _check_result("fail", f"alembic current {current!r} != head {head!r}")
    except Exception as exc:  # noqa: BLE001 - un-migrated schemas fail loudly
        return _check_result("fail", f"alembic check failed: {type(exc).__name__}")


def _check_adapters() -> dict[str, str]:
    """Readiness: Shopify and Odoo adapters are configured (fail-closed)."""
    settings = get_settings()
    missing = []
    if not settings.shopify_shop_name or not settings.shopify_access_token:
        missing.append("shopify")
    if not settings.odoo_base_url or not settings.odoo_api_key:
        missing.append("odoo")
    if missing:
        return _check_result("fail", f"missing adapter config: {', '.join(missing)}")
    return _check_result("ok", "shopify, odoo configured")


def _check_worker_heartbeat() -> dict[str, str]:
    """Readiness: the worker heartbeats within the 30-second alert window."""
    try:
        with SessionLocal() as session:
            row = (
                session.execute(
                    select(RuntimeHeartbeat)
                    .where(RuntimeHeartbeat.process_name == "worker")
                    .order_by(RuntimeHeartbeat.heartbeat_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        if row is None:
            return _check_result("fail", "no worker heartbeat recorded")
        age = max(0, int((utc_now() - row.heartbeat_at).total_seconds()))
        if age <= 30:
            return _check_result("ok", f"worker heartbeat {age}s ago")
        return _check_result("fail", f"worker heartbeat stale ({age}s)")
    except Exception as exc:  # noqa: BLE001 - probe endpoints must not crash
        return _check_result("fail", f"worker heartbeat check failed: {type(exc).__name__}")


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
    app.include_router(v1.me.router)
    app.include_router(v1.ops.router)
    app.include_router(v1.procurements.router)
    app.include_router(v1.reconciliations.router)
    app.include_router(v1.return_cases.router)
    app.include_router(v1.sales_orders.router)
    app.include_router(v1.workflows.router)
    app.include_router(v1.webhooks.router)

    @app.get("/livez", tags=["ops"])
    def livez() -> dict[str, str]:
        """Liveness probe: the API process is up (no dependency checks)."""
        return {"status": "ok"}

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Backward-compatible alias of /livez."""
        return livez()

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        """Readiness: database, Alembic head, adapter config and worker heartbeat.

        200 when every check passes, 503 with the per-check breakdown otherwise
        (this is the compose API healthcheck contract, WP3).
        """
        checks = {
            "database": _check_database(),
            "alembic": _check_alembic(),
            "adapters": _check_adapters(),
            "worker": _check_worker_heartbeat(),
        }
        ready = all(check.get("status") == "ok" for check in checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ok" if ready else "not_ready", "checks": checks},
        )

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        """Prometheus exposition of the shared metrics registry."""
        return Response(
            content=generate_latest(METRICS_REGISTRY),
            media_type="text/plain; version=0.0.4",
        )

    return app


app = create_app()
