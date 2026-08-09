"""Declarative base, shared naming conventions and reusable column mixins."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import DateTime, Integer, MetaData, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.time import utc_now
from app.core.uuid7 import uuid7

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Return the member *values* of an enum class.

    Used as ``values_callable`` for :class:`sqlalchemy.Enum` so the persisted
    VARCHAR strings are the lowercase vocabulary values (e.g. ``"planned"``)
    rather than the member names (e.g. ``"PLANNED"``).
    """
    return [member.value for member in enum_cls]


class Base(DeclarativeBase):
    """Project base class; metadata carries the shared naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """UTC created_at/updated_at timestamps (updated_at refreshes on update)."""

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class UUIDPkMixin:
    """UUIDv7 primary key."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


class VersionMixin:
    """Optimistic-lock version column for mutable aggregates."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


__all__ = [
    "Base",
    "NAMING_CONVENTION",
    "TimestampMixin",
    "UUIDPkMixin",
    "VersionMixin",
    "enum_values",
]
