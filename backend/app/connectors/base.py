"""Connector layer shared contracts: effect results, errors, and the channel protocol.

Design notes (v1):

- **Sync by default.** Connector calls are short, bounded HTTP requests
  executed from the DBOS worker. A sync ``httpx.Client`` keeps timeouts,
  retries and tests simple and avoids two divergent code paths. An async
  variant can be added later as a thin wrapper over the same request
  builders if a workflow ever needs asyncio-native execution; v1 ships one
  code path (see also ``app.connectors.shopify``).
- **Outcomes travel in ``EffectResult``, failures raise.** Per-operation
  methods return :class:`EffectResult` for every outcome whose remote state
  is known (succeeded / expected-conflict-failed). Transport failures and
  unexpected remote errors raise :class:`app.core.errors.ExternalSystemError`.
  On timeouts (or any transport failure that leaves the remote state
  ambiguous) connectors raise :class:`OutcomeUnknownError` -- a subclass of
  ``ExternalSystemError`` -- so the caller can record the effect as
  ``outcome_unknown`` and route it to reconciliation **instead of blindly
  retrying** (ADR-0007, architecture.md 5.4).
- **Statuses** are the exact strings from ``app.models.effect.EffectStatus``
  (``planned`` ... ``manual_reconciliation``); the connector itself only
  produces ``succeeded`` or ``failed`` and raises ``OutcomeUnknownError``
  for the unknown case. The remaining statuses are workflow/ledger states.
"""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.errors import ExternalSystemError
from app.models.effect import EffectStatus

EFFECT_STATUSES: frozenset[str] = frozenset(status.value for status in EffectStatus)

_ORIG_GETADDRINFO = socket.getaddrinfo


def _prefer_ipv4_getaddrinfo(*args: Any, **kwargs: Any):
    """Resolve hostnames preferring IPv4 (fallback to the original result).

    On this Windows host v2rayN's sing-box TUN has a broken IPv6 egress
    (see 环境运维手册: TUN IPv6 故障记录): DNS returns the AAAA record
    first and the IPv6 path dies with ``SSL: UNEXPECTED_EOF_WHILE_READING``
    while IPv4 works. Preferring AF_INET for connector traffic keeps
    Shopify/Odoo calls stable without touching the system network config.
    """
    result = _ORIG_GETADDRINFO(*args, **kwargs)
    try:
        ipv4 = [entry for entry in result if entry[0] == socket.AF_INET]
    except Exception:
        return result
    return ipv4 or result


def prefer_ipv4() -> None:
    """Install the IPv4-first resolver for this process (idempotent)."""
    if socket.getaddrinfo is not _prefer_ipv4_getaddrinfo:
        socket.getaddrinfo = _prefer_ipv4_getaddrinfo


"""Exact effect status vocabulary shared with the ledger."""

_TRUNCATE_LIMIT = 2000


def truncate(value: str, limit: int = _TRUNCATE_LIMIT) -> str:
    """Truncate an error/detail string to ``limit`` chars for safe storage."""
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def payload_hash(data: Any) -> str:
    """Canonical sha256 hex digest of a response/payload.

    JSON objects are serialized with sorted keys and compact separators so
    the digest is stable across byte-level formatting differences. Used for
    ``EffectResult.response_hash`` (drift detection during reconciliation).
    """
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
        raw = canonical.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class EffectResult:
    """Outcome of a single connector operation.

    ``ok`` is a convenience mirror of the status: ``True`` only for
    ``succeeded``. ``remote_reference`` is the external system's stable id
    (Shopify GID / Odoo record id) when known; ``response_hash`` is the
    canonical digest of the response payload.
    """

    ok: bool
    remote_reference: str | None
    status: str
    error: str | None
    response_hash: str | None

    def __post_init__(self) -> None:
        if self.status not in EFFECT_STATUSES:
            raise ValueError(
                f"invalid EffectResult status {self.status!r}; "
                f"expected one of {sorted(EFFECT_STATUSES)}"
            )
        if self.ok != (self.status == "succeeded"):
            raise ValueError("EffectResult.ok must mirror status == 'succeeded'")

    @classmethod
    def succeeded(cls, remote_reference: str | None, response_hash: str | None) -> EffectResult:
        """Build a success result."""
        return cls(
            ok=True,
            remote_reference=remote_reference,
            status="succeeded",
            error=None,
            response_hash=response_hash,
        )

    @classmethod
    def failed(cls, error: str, response_hash: str | None = None) -> EffectResult:
        """Build a known-failure result (e.g. an expected remote conflict)."""
        return cls(
            ok=False,
            remote_reference=None,
            status="failed",
            error=truncate(error),
            response_hash=response_hash,
        )


class ConnectorError(Exception):
    """Local connector-layer error (misconfiguration, programming misuse).

    Distinct from :class:`ExternalSystemError`, which signals a remote
    system failure. Raised e.g. when a required setting is missing or an
    operation is invoked without its mandatory arguments.
    """


class OutcomeUnknownError(ExternalSystemError):
    """Transport failure whose remote outcome cannot be determined.

    Raised on timeouts and other transport/5xx failures where the external
    system may or may not have applied the effect (at-least-once delivery).
    Carries ``status = "outcome_unknown"`` so callers can record the effect
    accordingly and defer to reconciliation instead of blind-retrying.
    """

    status: str = "outcome_unknown"


@runtime_checkable
class ChannelConnector(Protocol):
    """Structural contract implemented by every outbound connector.

    Concrete connectors add their own per-operation methods; each operation
    either returns :class:`EffectResult` or raises
    :class:`ExternalSystemError` / :class:`OutcomeUnknownError`. ``probe``
    is a read-only connectivity/auth check returning a dict; the exact keys
    are connector-specific (never a state-changing operation).
    """

    name: str

    def probe(self) -> dict[str, Any]: ...


__all__ = [
    "ChannelConnector",
    "ConnectorError",
    "EFFECT_STATUSES",
    "EffectResult",
    "OutcomeUnknownError",
    "payload_hash",
    "truncate",
]
