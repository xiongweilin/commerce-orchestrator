"""Worker-side privacy service (P7 五.3): pseudonymization + retention cleanup.

Provides the background-job entry points the worker runs:

- :func:`hmac_ref` — HMAC-SHA256 pseudonymous marker for customer refs that
  must be matchable but never re-derived (``COMMERCE_PII_HASH_KEY``).
- :func:`backfill_customer_refs` — encrypt legacy plaintext customer refs /
  shipping JSON into the :class:`SensitivePayload` vault and replace the
  plaintext columns with pseudonymous markers (idempotent, marker-prefixed).
- :func:`cleanup_expired_payloads` — after retention expires, clear the
  ciphertext first, then tombstone with ``deleted_at``; never logs contents.

The SQLAlchemy models (``sales_order.customer_ref``, ``return_case.customer_ref``,
``sales_order.shipping`` and the vault table) are provided by WP1.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.security import encrypt_payload
from app.core.time import utc_now
from app.models.order import SalesOrder
from app.models.returns import ReturnCase
from app.models.sensitive_payload import SensitivePayload

logger = get_logger("commerce.privacy")

PII_REF_MARKER = "pii:"
SHIPPING_MARKER_KEY = "sensitivePayloadId"

PURPOSE_CUSTOMER_REF = "customer_ref"
PURPOSE_SHIPPING = "shipping"
CLASSIFICATION_PII = "PII"
OWNER_COMMERCE = "commerce"


def _pii_hash_key() -> bytes:
    key = get_settings().pii_hash_key
    if not key:
        raise RuntimeError("COMMERCE_PII_HASH_KEY is not configured; privacy jobs refuse to run")
    return key.encode("utf-8")


def hmac_ref(value: str, *, key: bytes | None = None) -> str:
    """Pseudonymous HMAC marker for a customer ref (no reversible PII)."""
    if not value:
        raise ValidationError("cannot hash an empty customer ref")
    secret = key if key is not None else _pii_hash_key()
    digest = hmac_module.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{PII_REF_MARKER}{digest}"


def is_pseudonymous(value: str | None) -> bool:
    """True when the column already holds an HMAC marker."""
    return bool(value and value.startswith(PII_REF_MARKER))


def store_sensitive_payload(
    db,
    *,
    purpose: str,
    classification: str,
    owner: str,
    source_type: str,
    source_id: uuid.UUID | str,
    plaintext: str,
    expires_days: int | None = None,
) -> SensitivePayload:
    """Encrypt a plaintext into the vault and return the vault row."""
    settings = get_settings()
    expires_days = expires_days if expires_days is not None else settings.privacy_retention_days
    ciphertext = encrypt_payload(plaintext.encode("utf-8")).decode("ascii")
    payload = SensitivePayload(
        purpose=purpose,
        classification=classification,
        owner=owner,
        source_type=source_type,
        source_id=str(source_id),
        ciphertext=ciphertext,
        key_version="v1",
        expires_at=utc_now() + timedelta(days=expires_days),
    )
    db.add(payload)
    db.flush()
    return payload


@dataclass
class BackfillStats:
    """Counts for one encrypted-backfill pass."""

    sales_orders: int = 0
    return_cases: int = 0
    shipping_rows: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sales_orders + self.return_cases + self.shipping_rows


def backfill_customer_refs(
    db,
    *,
    batch: int = 100,
    expires_days: int | None = None,
) -> BackfillStats:
    """Encrypt legacy plaintext refs and replace them with HMAC markers.

    Idempotent: rows already carrying the ``pii:`` marker / shipping rows with
    a ``sensitivePayloadId`` are skipped.  The original plaintext lives only
    in the encrypted vault (never in logs).
    """
    stats = BackfillStats()

    orders = (
        db.execute(
            select(SalesOrder)
            .where(SalesOrder.customer_ref.is_not(None))
            .order_by(SalesOrder.created_at)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    for order in orders:
        try:
            if not is_pseudonymous(order.customer_ref):
                store_sensitive_payload(
                    db,
                    purpose=PURPOSE_CUSTOMER_REF,
                    classification=CLASSIFICATION_PII,
                    owner=OWNER_COMMERCE,
                    source_type="sales_order",
                    source_id=order.id,
                    plaintext=str(order.customer_ref),
                    expires_days=expires_days,
                )
                order.customer_ref = hmac_ref(str(order.customer_ref))
                stats.sales_orders += 1
            if isinstance(order.shipping, dict) and not order.shipping.get(SHIPPING_MARKER_KEY):
                raw = json.dumps(order.shipping, sort_keys=True, ensure_ascii=False)
                stored = store_sensitive_payload(
                    db,
                    purpose=PURPOSE_SHIPPING,
                    classification=CLASSIFICATION_PII,
                    owner=OWNER_COMMERCE,
                    source_type="sales_order",
                    source_id=order.id,
                    plaintext=raw,
                    expires_days=expires_days,
                )
                order.shipping = {SHIPPING_MARKER_KEY: str(stored.id), "encrypted": True}
                stats.shipping_rows += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the job
            stats.errors.append(f"sales_order {order.id}: {exc}")
            logger.exception("privacy_backfill_order_failed", order_id=str(order.id))

    cases = (
        db.execute(
            select(ReturnCase)
            .where(ReturnCase.customer_ref.is_not(None))
            .order_by(ReturnCase.created_at)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    for case in cases:
        try:
            if not is_pseudonymous(case.customer_ref):
                store_sensitive_payload(
                    db,
                    purpose=PURPOSE_CUSTOMER_REF,
                    classification=CLASSIFICATION_PII,
                    owner=OWNER_COMMERCE,
                    source_type="return_case",
                    source_id=case.id,
                    plaintext=str(case.customer_ref),
                    expires_days=expires_days,
                )
                case.customer_ref = hmac_ref(str(case.customer_ref))
                stats.return_cases += 1
        except Exception as exc:  # noqa: BLE001
            stats.errors.append(f"return_case {case.id}: {exc}")
            logger.exception("privacy_backfill_case_failed", case_id=str(case.id))

    db.flush()
    return stats


@dataclass
class CleanupStats:
    """Counts for one retention-cleanup pass."""

    cleared: int = 0
    errors: int = 0
    oldest_overdue_age_seconds: float | None = None


def cleanup_expired_payloads(
    db,
    *,
    batch: int = 100,
    now: datetime | None = None,
) -> CleanupStats:
    """Clear ciphertext of expired payloads and tombstone them.

    Order is fixed by the plan: clear the ciphertext first, then record the
    tombstone (``deleted_at``).  Contents are never logged.
    """
    now = now or utc_now()
    rows = (
        db.execute(
            select(SensitivePayload)
            .where(
                SensitivePayload.expires_at.is_not(None),
                SensitivePayload.expires_at <= now,
                SensitivePayload.deleted_at.is_(None),
            )
            .order_by(SensitivePayload.expires_at)
            .limit(batch)
        )
        .scalars()
        .all()
    )
    stats = CleanupStats()
    for row in rows:
        try:
            row.ciphertext = ""
            row.deleted_at = now
            stats.cleared += 1
            overdue = (now - row.expires_at).total_seconds()
            oldest = stats.oldest_overdue_age_seconds
            if oldest is None or overdue > oldest:
                stats.oldest_overdue_age_seconds = overdue
        except Exception:  # noqa: BLE001
            stats.errors += 1
    db.flush()
    return stats


def should_run_cleanup(
    *,
    last_run: datetime | None,
    now: datetime | None = None,
    interval_hours: int | None = None,
) -> bool:
    """True when the daily retention job is due (or has never run)."""
    now = now or utc_now()
    if last_run is None:
        return True
    settings = get_settings()
    hours = (
        interval_hours if interval_hours is not None else settings.privacy_cleanup_interval_hours
    )
    return (now - last_run) >= timedelta(hours=hours)


__all__ = [
    "BackfillStats",
    "CLASSIFICATION_PII",
    "CleanupStats",
    "OWNER_COMMERCE",
    "PII_REF_MARKER",
    "PURPOSE_CUSTOMER_REF",
    "PURPOSE_SHIPPING",
    "SHIPPING_MARKER_KEY",
    "backfill_customer_refs",
    "cleanup_expired_payloads",
    "hmac_ref",
    "is_pseudonymous",
    "should_run_cleanup",
    "store_sensitive_payload",
]
