"""Metabase read-only health probe.

Metabase is **never a fact owner** in this architecture (ADR-0003): it only
serves read-only projections from PostgreSQL. This connector therefore has
no write methods — only a read-only :meth:`MetabaseHealth.probe` used for
connectivity/monitoring.

There is no ``COMMERCE_METABASE_URL`` setting yet; when it is absent the
probe defaults to ``http://localhost:3001`` in ``dev`` and reports a
graceful "not configured" result otherwise (never raises for unreachability).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app import __version__ as APP_VERSION
from app.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger("commerce.connectors.metabase")

_DEV_DEFAULT_URL = "http://localhost:3001"


class MetabaseHealth:
    """Read-only Metabase health check (no write methods by design)."""

    name = "metabase"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    def _configured_base_url(self) -> str | None:
        """Resolve the Metabase base URL, or ``None`` when not configured."""
        configured = getattr(self.settings, "metabase_url", "").strip()
        if configured:
            return configured.rstrip("/")
        if self.settings.environment == "dev":
            return _DEV_DEFAULT_URL
        return None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers=self._request_headers(),
            )
        return self._client

    def _request_headers(self) -> dict[str, str]:
        return {"User-Agent": f"commerce-orchestrator/{APP_VERSION}"}

    def close(self) -> None:
        """Close the owned HTTP client (no-op for injected clients)."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def probe(self) -> dict[str, Any]:
        """Read-only health check of ``GET {base}/api/health``.

        Never raises for unreachability; returns
        ``{"ok": bool, "configured": bool, "version": str|None, "detail": str}``.
        """
        base_url = self._configured_base_url()
        if base_url is None:
            return {
                "ok": False,
                "configured": False,
                "version": None,
                "detail": (
                    "Metabase not configured: COMMERCE_METABASE_URL unset and "
                    "COMMERCE_ENVIRONMENT != dev; no health probe attempted"
                ),
            }
        try:
            response = self._get_client().get(
                f"{base_url}/api/health", headers=self._request_headers()
            )
        except httpx.TransportError as exc:
            logger.warning("metabase_probe_unreachable", error=str(exc))
            return {
                "ok": False,
                "configured": True,
                "version": None,
                "detail": f"Metabase unreachable: {type(exc).__name__}",
            }
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
        if response.status_code == 200 and isinstance(body, dict):
            return {
                "ok": True,
                "configured": True,
                "version": body.get("version"),
                "detail": f"Metabase healthy ({body.get('status', 'ok')})",
            }
        return {
            "ok": False,
            "configured": True,
            "version": None,
            "detail": f"Metabase /api/health returned HTTP {response.status_code}",
        }


__all__ = ["MetabaseHealth"]
