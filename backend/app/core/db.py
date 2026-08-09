"""Sync SQLAlchemy 2.0 engine, session factory and FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.base import Base, TimestampMixin, UUIDPkMixin, VersionMixin

engine = create_engine(get_settings().database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


__all__ = [
    "Base",
    "Session",
    "SessionLocal",
    "TimestampMixin",
    "UUIDPkMixin",
    "VersionMixin",
    "engine",
    "get_session",
]
