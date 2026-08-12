"""Shared pytest fixtures for the commerce-orchestrator backend.

The test environment is configured BEFORE any ``app`` module is imported so
that the cached settings singleton and the import-time engine in
``app.core.db`` are built against an isolated sqlite file.  Each test gets a
fresh schema (``create_all`` / ``drop_all``) on the shared session-scoped
engine, plus helpers to create users with roles and to mint JWTs.
"""

from __future__ import annotations

import base64
import contextlib
import os
import secrets
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make the backend package importable when pytest is run from the repo root
# (``uv run pytest`` does not place the backend directory on sys.path).
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Test environment (must precede any app import)
# ---------------------------------------------------------------------------

TEST_DB_FILENAME = f"test_{secrets.token_hex(4)}.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_FILENAME}"

os.environ["COMMERCE_DATABASE_URL"] = TEST_DB_URL
os.environ["COMMERCE_DBOS_SYSTEM_DATABASE_URL"] = "sqlite:///./test_dbos.db"
os.environ["COMMERCE_JWT_SECRET"] = "test-jwt-secret-not-for-production"
os.environ["COMMERCE_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(os.urandom(32)).decode()
os.environ["COMMERCE_ENVIRONMENT"] = "dev"
os.environ["COMMERCE_LOG_LEVEL"] = "WARNING"
os.environ["COMMERCE_SHOPIFY_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["COMMERCE_SHOPIFY_SHOP_NAME"] = "test-shop"
os.environ["COMMERCE_SHOPIFY_ACCESS_TOKEN"] = "shpat_test_token"
# Test isolation: never let a real root `.env` inject client credentials.
# pydantic-settings prefers env vars over the `.env` file, so these empty
# values keep every Settings instance free of real Shopify OAuth credentials
# regardless of the pytest working directory.  Without this, running the
# suite from the repo root reads COMMERCE_SHOPIFY_CLIENT_ID/SECRET from the
# real `.env` and triggers a client-credentials token exchange (network).
os.environ["COMMERCE_SHOPIFY_CLIENT_ID"] = ""
os.environ["COMMERCE_SHOPIFY_CLIENT_SECRET"] = ""
os.environ["COMMERCE_ODOO_BASE_URL"] = "http://odoo.test"
os.environ["COMMERCE_ODOO_API_KEY"] = "odoo-test-key"
os.environ["COMMERCE_ODOO_DB"] = "test-db"

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import encode_jwt  # noqa: E402
from app.core.uuid7 import uuid7  # noqa: E402
from app.models.identity import Role, RoleAssignment, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Iterator[None]:
    """Dispose the engine and remove the sqlite file at the session end."""
    yield
    engine.dispose()
    for suffix in ("", "-shm", "-wal"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(TEST_DB_FILENAME + suffix)


@pytest.fixture
def db() -> Iterator:
    """A session on the shared engine with a fresh schema per test."""
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def make_user(db) -> Callable[..., uuid.UUID]:
    """Create a user with the given roles and return the user id."""

    def _make_user(roles: list[str], *, email: str | None = None) -> uuid.UUID:
        user_id = uuid7()
        db.add(
            User(
                id=user_id,
                email=email or f"user-{user_id}@test.local",
                display_name="Test User",
            )
        )
        for role in roles:
            db.add(RoleAssignment(user_id=user_id, role=Role(role), scope="*"))
        db.commit()
        return user_id

    return _make_user


@pytest.fixture
def jwt_for() -> Callable[[uuid.UUID, list[str]], str]:
    """Mint a valid JWT for a user id (role claims are informational)."""

    def _jwt(user_id: uuid.UUID, roles: list[str]) -> str:
        return encode_jwt(str(user_id), roles)

    return _jwt


@pytest.fixture
def auth_headers(jwt_for) -> Callable[[uuid.UUID, list[str]], dict[str, str]]:
    """Return Bearer auth headers for a user."""

    def _headers(user_id: uuid.UUID, roles: list[str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {jwt_for(user_id, roles or [])}"}

    return _headers


@pytest.fixture
def client(db) -> Iterator[TestClient]:
    """A TestClient backed by the per-test sqlite schema."""
    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def clean_outbox_registries() -> Iterator[None]:
    """Clear the module-level consumer routing/handler registries."""
    from app.services import outbox_inbox

    before_routes = dict(outbox_inbox._LOCAL_CONSUMER_ROUTING)
    before_handlers = dict(outbox_inbox.CONSUMER_HANDLERS)
    yield
    outbox_inbox._LOCAL_CONSUMER_ROUTING.clear()
    outbox_inbox._LOCAL_CONSUMER_ROUTING.update(before_routes)
    outbox_inbox.CONSUMER_HANDLERS.clear()
    outbox_inbox.CONSUMER_HANDLERS.update(before_handlers)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers so ``pytest -m`` and warnings stay clean."""
    config.addinivalue_line(
        "markers",
        "integration: requires a local PostgreSQL (Docker commerce-postgres); "
        "skipped when the database is unreachable.",
    )
    config.addinivalue_line(
        "markers",
        "dbos_integration: requires a local PostgreSQL and the DBOS runtime; "
        "skipped when either is unavailable.",
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remember pytest's exit status for the DBOS clean-exit path."""
    session.config._pytest_exitstatus = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Force a clean interpreter exit after the DBOS integration module ran.

    The in-process DBOS runtime leaves its executor thread blocked inside a
    long ``DBOS.recv`` even after ``DBOS.destroy()``; Python's normal shutdown
    would hang joining that non-daemon thread.  The DBOS fixture marks the
    shared config object; ``pytest_unconfigure`` is the last pytest hook (it
    runs after the terminal reporter's summary), so we exit with pytest's own
    status without truncating the report.
    """
    if getattr(config, "_dbos_force_exit", False):
        import os
        import sys

        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(getattr(config, "_pytest_exitstatus", 0))


__all__ = ["TEST_DB_FILENAME", "TEST_DB_URL"]
