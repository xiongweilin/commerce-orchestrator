"""Dify workflow connector: LLM-generated catalog change suggestions (P6).

Role in the architecture (see the P6 plan): the Dify workflow is a
**proposal generator only**. It reads sanitized customer feedback and emits a
structured JSON catalog-change suggestion (``sku`` / ``title`` / ``category`` /
``description`` / ``quality_actions`` / ``availability_actions``). It never
approves and never executes anything; approval and execution stay in the
commerce-orchestrator workflows (catalog-revision gate by ``catalog_owner``).

Wire shape (verified against Dify 1.13.3 service API):

- ``POST {base}/v1/workflows/{workflow_id}/run`` with a JSON body
  ``{"inputs": {...}, "response_mode": "blocking", "user": "..."}``.
- ``Authorization: Bearer <app api key>``.
- Success: HTTP 200 with ``data.outputs.<output_variable>`` holding the
  workflow's end-node output (a JSON string here, parsed by this connector).
  ``data.status == "failed"`` with HTTP 200 means the workflow ran but a node
  failed (e.g. the configured model provider is unavailable); that is a
  definitive, non-transport failure -> :class:`ExternalSystemError`.
- Transport failures and 5xx/408 leave the remote outcome ambiguous ->
  :class:`OutcomeUnknownError` (same contract as the other connectors).

The endpoint is deliberately **read-only from our side**: it triggers an LLM
generation, it does not mutate any external ledger. Because a timeout may
still have consumed quota / started a run, ambiguous failures raise
``OutcomeUnknownError`` instead of blind-retrying.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app import __version__ as APP_VERSION
from app.config import Settings, get_settings
from app.connectors.base import ConnectorError, OutcomeUnknownError, prefer_ipv4, truncate
from app.core.errors import ExternalSystemError
from app.core.logging import get_logger

logger = get_logger("commerce.connectors.dify")

# 与本机其它连接器一致：TUN IPv6 出口异常，DNS 解析优先 IPv4。
prefer_ipv4()

EXPECTED_KEYS = (
    "sku",
    "title",
    "category",
    "description",
    "quality_actions",
    "availability_actions",
)
"""Keys the LLM output JSON must carry for a usable catalog suggestion."""


class DifyConnector:
    """Synchronous Dify workflow connector (proposal generation, no writes).

    Construction performs no network I/O; the ``httpx.Client`` is created
    lazily. Pass ``client`` to inject a mock/recorded client in tests.
    """

    name = "dify"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.dify_base_url.strip().rstrip("/")
        self.workflow_id = self.settings.dify_workflow_id.strip()
        self.api_key = self.settings.dify_api_key.strip()
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ #
    # Configuration and HTTP plumbing
    # ------------------------------------------------------------------ #

    def _require_configured(self) -> None:
        if not self.base_url or not self.workflow_id or not self.api_key:
            raise ConnectorError(
                "Dify connector is not configured: set COMMERCE_DIFY_BASE_URL, "
                "COMMERCE_DIFY_WORKFLOW_ID and COMMERCE_DIFY_API_KEY"
            )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers=self._request_headers(),
                # 直连本机 Dify，不经系统代理。
                trust_env=False,
            )
        return self._client

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"commerce-orchestrator/{APP_VERSION}",
        }

    def close(self) -> None:
        """Close the owned HTTP client (no-op for injected clients)."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    # Operations
    # ------------------------------------------------------------------ #

    def generate_catalog_suggestion(
        self,
        feedback_text: str,
        *,
        feedback_type: str | None = None,
        sku: str | None = None,
    ) -> dict[str, Any]:
        """Run the Dify workflow and return the parsed catalog suggestion.

        ``feedback_text`` is sanitized feedback (may concatenate several
        items). ``feedback_type`` / ``sku`` are optional workflow inputs.
        Returns the validated proposal dict with keys
        ``sku / title / category / description / quality_actions /
        availability_actions``.

        Raises :class:`ExternalSystemError` for definitive Dify failures
        (auth, workflow/node failure, invalid/unparsable output) and
        :class:`OutcomeUnknownError` for ambiguous transport/5xx failures.
        """
        self._require_configured()
        inputs: dict[str, Any] = {"feedback_text": feedback_text}
        if feedback_type:
            inputs["feedback_type"] = feedback_type
        if sku:
            inputs["sku"] = sku
        payload = {
            "inputs": inputs,
            "response_mode": "blocking",
            "user": "commerce-orchestrator",
        }
        url = f"{self.base_url}/v1/workflows/{self.workflow_id}/run"

        try:
            response = self._get_client().post(url, json=payload, headers=self._request_headers())
        except httpx.TransportError as exc:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport error"
            logger.warning(
                "dify_run_outcome_unknown",
                workflow_id=self.workflow_id,
                error_type=kind,
                error=str(exc),
            )
            raise OutcomeUnknownError(
                f"Dify workflow {self.workflow_id} run failed with {kind} "
                f"({type(exc).__name__}); outcome unknown — do not blind-retry, "
                "route to reconciliation"
            ) from exc

        if response.status_code != 200:
            return self._raise_for_error(response)
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} returned HTTP 200 with non-JSON body"
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} returned an unexpected payload shape"
            )

        data = body["data"]
        status = data.get("status")
        if status != "succeeded":
            error = truncate(
                str(data.get("error") or data.get("error_message") or "unknown error"), 2000
            )
            logger.warning(
                "dify_workflow_failed", workflow_id=self.workflow_id, status=status, error=error
            )
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} failed (status={status}): {error}"
            )

        outputs = data.get("outputs") or {}
        raw = outputs.get("result")
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ExternalSystemError(
                    f"Dify workflow {self.workflow_id} returned non-JSON result output; "
                    "expected the LLM to emit a strict JSON object"
                ) from exc
        else:
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} returned a missing/invalid result output "
                f"(type={type(raw).__name__})"
            )
        if not isinstance(parsed, dict):
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} result output is not a JSON object"
            )
        missing = [key for key in EXPECTED_KEYS if key not in parsed]
        if missing:
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} result is missing required keys: "
                f"{', '.join(missing)}"
            )
        logger.info("dify_suggestion_generated", workflow_id=self.workflow_id)
        return parsed

    def _raise_for_error(self, response: httpx.Response) -> dict[str, Any]:
        """Classify a non-200 response into the right exception (never returns)."""
        status = response.status_code
        body_text = truncate(response.text, 800)
        message = body_text
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                message = truncate(
                    str(parsed.get("message") or parsed.get("error") or body_text), 800
                )
        except json.JSONDecodeError:
            pass

        if status in (401, 403):
            logger.warning("dify_auth_failed", workflow_id=self.workflow_id, status=status)
            raise ExternalSystemError(
                f"Dify workflow {self.workflow_id} authentication/authorization failed "
                f"(HTTP {status}): {message}"
            )
        if status == 408 or status >= 500:
            logger.warning(
                "dify_request_outcome_unknown", workflow_id=self.workflow_id, status=status
            )
            raise OutcomeUnknownError(
                f"Dify workflow {self.workflow_id} returned HTTP {status} without a parseable "
                "error object; outcome unknown — route to reconciliation"
            )
        raise ExternalSystemError(
            f"Dify workflow {self.workflow_id} failed (HTTP {status}): {message}"
        )

    def probe(self) -> dict[str, Any]:
        """Read-only configuration/connectivity check.

        Never raises for unreachability; returns
        ``{"ok": bool, "configured": bool, "detail": str}``.
        """
        if not self.base_url or not self.workflow_id or not self.api_key:
            return {
                "ok": False,
                "configured": False,
                "detail": (
                    "Dify not configured: COMMERCE_DIFY_BASE_URL / "
                    "COMMERCE_DIFY_WORKFLOW_ID / COMMERCE_DIFY_API_KEY required"
                ),
            }
        try:
            response = self._get_client().get(f"{self.base_url}/console/api/setup")
        except httpx.TransportError as exc:
            logger.warning("dify_probe_unreachable", error=str(exc))
            return {
                "ok": False,
                "configured": True,
                "detail": f"Dify unreachable: {type(exc).__name__}",
            }
        if response.status_code == 200:
            return {"ok": True, "configured": True, "detail": "Dify reachable"}
        return {
            "ok": False,
            "configured": True,
            "detail": f"Dify /console/api/setup returned HTTP {response.status_code}",
        }


__all__ = ["DifyConnector"]
