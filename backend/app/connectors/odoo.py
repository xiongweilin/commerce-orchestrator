"""Odoo 19 External JSON-2 API connector (authoritative ledger writes).

Wire shape (verified against the official Odoo 19 external API reference):

- ``POST {base}/json/2/<model>/<method>`` with a JSON body.
- ``Authorization: bearer <api key>`` (lower-case ``bearer`` per the docs).
- ``Content-Type: application/json; charset=utf-8``.
- ``X-Odoo-Database: <db>`` when ``COMMERCE_ODOO_DB`` is set (required when
  a single Odoo server hosts multiple databases).
- The body is a **plain named-parameter object** (``ids``, ``context`` and
  the method's own parameters as keys) -- the older
  ``{"jsonrpc": "2.0", "method": "call"}`` envelope is intentionally not
  used; the Odoo 19 docs define the plain shape (ADR-0008).
- Success: HTTP 200 with the JSON-serialized method return value. Error:
  4xx/5xx with a JSON error object ``{"name", "message", "arguments",
  "context", "debug"}``. Each call runs in its own server-side SQL
  transaction, rolled back on error, so a parseable Odoo error means the
  effect was definitively **not applied**.

**P0 gate (ADR-0008)**: JSON-2 availability on Odoo 19 Community is NOT
assumed. :meth:`OdooConnector.probe` must pass at deployment before any
write path is enabled; on failure it raises with a clear pointer to the
ADR-0008 fallback (minimal integration module; XML-RPC intentionally not
expanded). Operation parameters (field names, ``update_quantity`` semantics)
are also verified against the deployed instance during this gate.

**Atomicity**: every operation is exactly one JSON-2 call (one server-side
transaction). No multi-call transactions from the connector; multi-step
flows are composed by workflows as separate effects.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app import __version__ as APP_VERSION
from app.config import Settings, get_settings
from app.connectors.base import (
    ConnectorError,
    EffectResult,
    OutcomeUnknownError,
    payload_hash,
    prefer_ipv4,
    truncate,
)
from app.core.errors import ExternalSystemError
from app.core.logging import get_logger
from app.core.telemetry import get_tracer

logger = get_logger("commerce.connectors.odoo")

# 与 Shopify 连接器一致：本机 TUN IPv6 出口异常，DNS 解析优先 IPv4。
prefer_ipv4()

_CONFLICT_MARKERS = (
    "already exists",
    "duplicate",
    "already confirmed",
    "already been confirmed",
    "already validated",
    "already been validated",
    "already posted",
    "already been posted",
    "already done",
    "already been done",
)
"""Best-effort Odoo error-message substrings treated as expected conflicts.

Only duplicate/unique violations and already-* replay states are treated as
expected conflicts (returning ``EffectResult(ok=False, status="failed")``).
Business refusals such as "cannot confirm" raise ExternalSystemError so the
workflow explicitly decides how to handle them.
"""

_ADR8_FALLBACK_HINT = (
    "JSON-2 availability on Odoo 19 Community must be runtime-verified (P0 gate); "
    "if unavailable, implement the ADR-0008 minimal integration module — "
    "XML-RPC is intentionally NOT expanded"
)


class OdooApiError(ExternalSystemError):
    """Odoo returned a parseable error object (its transaction rolled back).

    ``error_body`` holds the raw ``{"name", "message", "arguments",
    "context", "debug"}`` object so callers can inspect/classify it.
    """

    def __init__(self, detail: str, error_body: dict[str, Any]) -> None:
        super().__init__(detail)
        self.error_body = error_body


class OdooConnector:
    """Synchronous Odoo 19 JSON-2 connector.

    Construction performs no network I/O; the ``httpx.Client`` is created
    lazily. Pass ``client`` to inject a mock/recorded client in tests.
    """

    name = "odoo"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.odoo_base_url.strip().rstrip("/")
        self.api_key = self.settings.odoo_api_key.strip()
        self.db = self.settings.odoo_db.strip()
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------ #
    # Configuration and HTTP plumbing
    # ------------------------------------------------------------------ #

    def _require_configured(self) -> None:
        if not self.base_url or not self.api_key:
            raise ConnectorError(
                "Odoo connector is not configured: set COMMERCE_ODOO_BASE_URL and "
                "COMMERCE_ODOO_API_KEY (COMMERCE_ODOO_DB when the server hosts "
                "multiple databases)"
            )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers=self._request_headers(),
                # 直连目标 Odoo（公网或 tailnet），不经系统代理。
                trust_env=False,
            )
        return self._client

    def _request_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Authorization": f"bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": f"commerce-orchestrator/{APP_VERSION}",
        }
        if self.db:
            headers["X-Odoo-Database"] = self.db
        return headers

    def close(self) -> None:
        """Close the owned HTTP client (no-op for injected clients)."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _post(self, model: str, method: str, params: dict[str, Any]) -> Any:
        """Execute one JSON-2 call and return the parsed method result.

        Raises OutcomeUnknownError for ambiguous transport/5xx failures and
        OdooApiError / ExternalSystemError for definitive failures.
        """
        self._require_configured()
        url = f"{self.base_url}/json/2/{model}/{method}"
        tracer = get_tracer()
        with tracer.start_as_current_span(f"connector.odoo.{model}.{method}") as span:
            span.set_attribute("odoo.model", model)
            span.set_attribute("odoo.method", method)
            try:
                response = self._get_client().post(
                    url, json=params, headers=self._request_headers()
                )
            except httpx.TransportError as exc:
                span.record_exception(exc)
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport error"
                logger.warning(
                    "odoo_request_outcome_unknown",
                    model=model,
                    method=method,
                    error_type=kind,
                    error=str(exc),
                )
                raise OutcomeUnknownError(
                    f"Odoo {model}/{method} failed with {kind} ({type(exc).__name__}); "
                    "outcome unknown — do not blind-retry, route to reconciliation"
                ) from exc
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError as exc:
                    raise ExternalSystemError(
                        f"Odoo {model}/{method} returned HTTP 200 with non-JSON body"
                    ) from exc
            return self._raise_for_error(response, model=model, method=method)

    def _raise_for_error(self, response: httpx.Response, *, model: str, method: str) -> Any:
        """Classify a non-2xx response into the right exception (never returns)."""
        status = response.status_code
        body_text = truncate(response.text, 800)
        error_body: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict) and ("message" in parsed or "name" in parsed):
                error_body = parsed
        except json.JSONDecodeError:
            pass

        if status in (401, 403):
            message = truncate(str(error_body.get("message", ""))) if error_body else body_text
            raise ExternalSystemError(
                f"Odoo {model}/{method} authentication/authorization failed "
                f"(HTTP {status}): {message}"
            )
        if error_body is not None:
            # A parseable Odoo error means the per-call transaction rolled
            # back: the effect was definitively NOT applied.
            message = truncate(str(error_body.get("message", "")), 2000)
            name = error_body.get("name", "OdooError")
            logger.warning("odoo_api_error", model=model, method=method, status=status, name=name)
            raise OdooApiError(
                f"Odoo {model}/{method} failed (HTTP {status}, {name}): {message}",
                error_body=error_body,
            )
        if status == 408 or status >= 500:
            logger.warning(
                "odoo_request_outcome_unknown", model=model, method=method, status=status
            )
            raise OutcomeUnknownError(
                f"Odoo {model}/{method} returned HTTP {status} without a parseable "
                "error object; outcome unknown — route to reconciliation"
            )
        raise ExternalSystemError(f"Odoo {model}/{method} failed (HTTP {status}): {body_text}")

    def _write(
        self,
        model: str,
        method: str,
        params: dict[str, Any],
        *,
        operation: str,
        odoo_id: int | None = None,
    ) -> EffectResult:
        """Execute a write method, mapping conflicts to failed results."""
        try:
            result = self._post(model, method, params)
        except OdooApiError as exc:
            message = str(exc.error_body.get("message", ""))
            lowered = message.lower()
            if any(marker in lowered for marker in _CONFLICT_MARKERS):
                logger.info("odoo_expected_conflict", operation=operation, detail=truncate(message))
                return EffectResult.failed(
                    error=f"Odoo {operation} conflict: {truncate(message, 1000)}",
                    response_hash=payload_hash(exc.error_body),
                )
            raise
        if odoo_id is not None:
            remote_reference = str(odoo_id)
        elif isinstance(result, list) and len(result) == 1:
            # JSON-2 create returns the new record id list, e.g. [12].
            remote_reference = str(result[0])
        else:
            remote_reference = str(result)
        logger.info("odoo_effect_succeeded", operation=operation, remote_reference=remote_reference)
        return EffectResult.succeeded(
            remote_reference=remote_reference, response_hash=payload_hash(result)
        )

    # ------------------------------------------------------------------ #
    # Connectivity / auth probe (read-only, P0 gate)
    # ------------------------------------------------------------------ #

    def probe(self) -> dict[str, Any]:
        """Runtime JSON-2 availability + auth check (P0 gate, ADR-0008).

        Executes a harmless read (``res.users/search_read`` with limit 1).
        On success returns ``{"ok": True, "json2_available": True, "detail"}``.
        On failure it **raises** (config missing -> ConnectorError; JSON-2
        route/auth/transport -> ExternalSystemError) with a clear pointer to
        the ADR-0008 fallback. Write paths must not be enabled until this
        passes against the real Odoo 19 Community container.
        """
        self._require_configured()
        try:
            result = self._post("res.users", "search_read", {"fields": ["id"], "limit": 1})
        except OutcomeUnknownError as exc:
            raise ExternalSystemError(
                f"Odoo JSON-2 probe could not determine availability: {exc.detail}. "
                f"{_ADR8_FALLBACK_HINT}"
            ) from exc
        except ExternalSystemError as exc:
            raise ExternalSystemError(
                f"Odoo JSON-2 probe failed: {exc.detail}. {_ADR8_FALLBACK_HINT}"
            ) from exc
        return {
            "ok": True,
            "json2_available": True,
            "detail": f"res.users/search_read ok (limit 1) -> {result!r}",
        }

    # ------------------------------------------------------------------ #
    # Product
    # ------------------------------------------------------------------ #

    def create_product(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a product (``product.template`` create; one variant per SKU).

        Odoo 19 JSON-2 ``create`` takes ``vals_list`` (list of value dicts);
        a single-record create sends ``{"vals_list": [values]}``.
        """
        return self._write(
            "product.template", "create", {"vals_list": [values]}, operation="product_create"
        )

    def update_product(
        self, odoo_id: int, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Update a product (``product.template`` write by record id).

        Odoo 19 JSON-2 ``write`` takes positional ``ids`` plus ``vals``.
        """
        return self._write(
            "product.template",
            "write",
            {"ids": [odoo_id], "vals": values},
            operation="product_update",
            odoo_id=odoo_id,
        )

    # ------------------------------------------------------------------ #
    # Sales orders
    # ------------------------------------------------------------------ #

    def create_sale_order(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a draft sales order (``sale.order`` create)."""
        return self._write(
            "sale.order", "create", {"vals_list": [values]}, operation="sale_order_create"
        )

    def confirm_sale_order(
        self, odoo_id: int, *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Confirm a sales order (``sale.order`` ``action_confirm``)."""
        return self._write(
            "sale.order",
            "action_confirm",
            {"ids": [odoo_id]},
            operation="sale_order_confirm",
            odoo_id=odoo_id,
        )

    # ------------------------------------------------------------------ #
    # Inventory / stock
    # ------------------------------------------------------------------ #

    def create_stock_move(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a stock move (``stock.move`` create)."""
        return self._write(
            "stock.move", "create", {"vals_list": [values]}, operation="stock_move_create"
        )

    def create_picking(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a picking (``stock.picking`` create)."""
        return self._write(
            "stock.picking", "create", {"vals_list": [values]}, operation="picking_create"
        )

    def validate_picking(self, odoo_id: int, *, idempotency_key: str | None = None) -> EffectResult:
        """Validate a picking (``stock.picking`` ``button_validate``, Odoo 19)."""
        return self._write(
            "stock.picking",
            "button_validate",
            {"ids": [odoo_id]},
            operation="picking_validate",
            odoo_id=odoo_id,
        )

    def receive_transfer(self, odoo_id: int, *, idempotency_key: str | None = None) -> EffectResult:
        """Validate an incoming transfer / receipt (``stock.picking`` ``button_validate``).

        Single narrow call; over/under-receipt wizard handling is out of
        scope here — the caller must pass a picking whose quantities are
        complete (ADR-0008: atomic single-method actions).
        """
        return self._write(
            "stock.picking",
            "action_done",
            {"ids": [odoo_id]},
            operation="receive_transfer",
            odoo_id=odoo_id,
        )

    def update_quantity(
        self,
        product_id: int,
        location_id: int,
        quantity: int | float,
        *,
        extra_values: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> EffectResult:
        """Set the absolute on-hand quantity (P3-verified, Odoo 19 Community).

        P3 finding (2026-08-10, see ADR-0008 实测记录): ``stock.quant`` has no
        ``reason`` field, and ``inventory_diff_quantity`` is a readonly stored
        computed field whose value is silently dropped on ``create`` (on-hand
        unchanged). The supported atomic path is a single ``stock.quant`` write
        of the computed-with-inverse ``inventory_quantity_auto_apply`` with
        ``context: {"inventory_mode": True}``: the inverse sets the counted
        quantity and calls ``action_apply_inventory`` in the same server-side
        transaction (one JSON-2 call = one atomic effect).

        ``inventory_mode`` requires the calling user to have
        ``stock.group_stock_user`` (admin implies it); production API keys must
        carry that group. This method resolves the target quant with a read-only
        search first (creating an inert empty quant placeholder only if none
        exists), then performs the single atomic write above. Re-applying the
        current quantity is a no-op on the server side (inverse skips equal
        values), so the call is idempotent.
        """
        quants = self._post(
            "stock.quant",
            "search_read",
            {
                "domain": [
                    ["product_id", "=", product_id],
                    ["location_id", "=", location_id],
                ],
                "fields": ["id"],
                "limit": 1,
            },
        )
        if quants:
            quant_id = int(quants[0]["id"])
        else:
            created = self._post(
                "stock.quant",
                "create",
                {"vals_list": [{"product_id": product_id, "location_id": location_id}]},
            )
            quant_id = int(created[0])
        values: dict[str, Any] = {"inventory_quantity_auto_apply": quantity}
        if extra_values:
            values.update(extra_values)
        return self._write(
            "stock.quant",
            "write",
            {"ids": [quant_id], "vals": values, "context": {"inventory_mode": True}},
            operation="update_quantity",
            odoo_id=quant_id,
        )

    # ------------------------------------------------------------------ #
    # Invoices / credit notes / bills
    # ------------------------------------------------------------------ #

    def create_invoice(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a customer invoice (``account.move`` ``move_type`` out_invoice)."""
        body = dict(values)
        body.setdefault("move_type", "out_invoice")
        return self._write(
            "account.move", "create", {"vals_list": [body]}, operation="invoice_create"
        )

    def validate_invoice(self, odoo_id: int, *, idempotency_key: str | None = None) -> EffectResult:
        """Post an invoice (``account.move`` ``action_post``)."""
        return self._write(
            "account.move",
            "action_post",
            {"ids": [odoo_id]},
            operation="invoice_validate",
            odoo_id=odoo_id,
        )

    def create_credit_note(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a credit note (``account.move`` ``move_type`` out_refund)."""
        body = dict(values)
        body.setdefault("move_type", "out_refund")
        return self._write(
            "account.move", "create", {"vals_list": [body]}, operation="credit_note_create"
        )

    def validate_credit_note(
        self, odoo_id: int, *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Post a credit note (``account.move`` ``action_post``)."""
        return self._write(
            "account.move",
            "action_post",
            {"ids": [odoo_id]},
            operation="credit_note_validate",
            odoo_id=odoo_id,
        )

    def create_po(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a purchase order (``purchase.order`` create)."""
        return self._write(
            "purchase.order", "create", {"vals_list": [values]}, operation="po_create"
        )

    def confirm_po(self, odoo_id: int, *, idempotency_key: str | None = None) -> EffectResult:
        """Confirm a purchase order (``purchase.order`` ``button_confirm``)."""
        return self._write(
            "purchase.order",
            "button_confirm",
            {"ids": [odoo_id]},
            operation="po_confirm",
            odoo_id=odoo_id,
        )

    def create_bill(
        self, values: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a vendor bill (``account.move`` ``move_type`` in_invoice)."""
        body = dict(values)
        body.setdefault("move_type", "in_invoice")
        return self._write("account.move", "create", {"vals_list": [body]}, operation="bill_create")


__all__ = ["OdooApiError", "OdooConnector"]
