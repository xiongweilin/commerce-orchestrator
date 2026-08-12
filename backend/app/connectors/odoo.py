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
from app.core.errors import ExternalSystemError, RetryableEffectError
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

_CO_MARKER_PREFIX = "CO:"
"""Idempotency marker prefix for Odoo create operations (plan 二.4).

Every create-carrying effect stamps ``CO:<intent_id>`` into a searchable
field (``client_order_ref`` / ``partner_ref`` / ``origin`` / ``ref``) so a
replayed intent is found by a read-before-create instead of duplicated.
"""


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
        if status == 429:
            # 429 is returned before the request was processed: the per-call
            # transaction did not run, so the effect was definitively NOT
            # applied — retrying the same intent is safe.
            raise RetryableEffectError(
                f"Odoo {model}/{method} rate limited (HTTP 429) before processing; "
                "the effect was not applied — retry is safe"
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
    # Read helpers (idempotency read-backs and reconciliation reads)
    # ------------------------------------------------------------------ #

    def _search(
        self,
        model: str,
        domain: list[list[Any]],
        fields: list[str],
        *,
        limit: int = 5,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Run a read-only ``search_read`` on a model (never state-changing)."""
        rows = self._post(
            model,
            "search_read",
            {"domain": domain, "fields": fields, "limit": limit, "offset": offset},
        )
        if not isinstance(rows, list):
            raise ExternalSystemError(
                f"Odoo {model}/search_read returned an unexpected shape: {type(rows).__name__}"
            )
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _search_one(
        self, model: str, domain: list[list[Any]], fields: list[str]
    ) -> dict[str, Any] | None:
        rows = self._search(model, domain, fields, limit=1)
        return rows[0] if rows else None

    def _read_state(self, model: str, odoo_id: int) -> str:
        """Read the ``state`` field of an Odoo record (idempotency pre-check)."""
        row = self._search_one(model, [["id", "=", int(odoo_id)]], ["id", "state"])
        if row is None:
            raise ExternalSystemError(f"Odoo {model} record {odoo_id} not found")
        return str(row.get("state") or "")

    def _co_marker(self, intent_id) -> str:
        return f"{_CO_MARKER_PREFIX}{intent_id}"

    def _apply_marker(
        self, values: dict[str, Any], intent_id, *, field: str
    ) -> tuple[dict[str, Any], str | None]:
        """Stamp ``CO:<intent_id>`` into ``values[field]`` and return it.

        When ``intent_id`` is None the values are returned untouched with a
        ``None`` marker. A pre-existing value that differs from the marker is
        a contract violation (fail-closed) so read-before-create stays sound.
        """
        if intent_id is None:
            return values, None
        marker = self._co_marker(intent_id)
        current = values.get(field)
        if current and str(current) != marker:
            raise ConnectorError(
                f"Odoo create values.{field} is {current!r}; expected the "
                f"CO:<intent_id> marker {marker!r} for idempotent create"
            )
        updated = dict(values)
        updated.setdefault(field, marker)
        return updated, marker

    def _replayed_result(self, remote_reference: str, facts: dict[str, Any]) -> EffectResult:
        """Build a replayed-success result for read-before-create/state hits."""
        logger.info("odoo_effect_replayed", remote_reference=remote_reference)
        return EffectResult.succeeded(
            remote_reference=remote_reference,
            response_hash=payload_hash(facts),
            replayed=True,
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
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a product idempotently by SKU (``default_code``).

        Odoo 19 JSON-2 ``create`` takes ``vals_list`` (list of value dicts);
        a single-record create sends ``{"vals_list": [values]}``. When an
        ``intent_id`` is carried (the WP5 effect seam) a product with the
        same ``default_code`` already existing is reported as
        ``succeeded(replayed=True)`` instead of duplicated; legacy callers
        without an intent keep the v1 single-write behavior.
        """
        sku = (values.get("default_code") or "").strip()
        if intent_id is not None and sku:
            existing = self._search_one(
                "product.template", [["default_code", "=", sku]], ["id", "default_code"]
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "product.template", "create", {"vals_list": [values]}, operation="product_create"
        )

    def update_product(
        self,
        odoo_id: int,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
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
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a draft sales order idempotently.

        Accepts Odoo-native ``partner_id`` + ``order_line`` values or the
        domain business fields (``items`` / ``customer_ref`` / ``currency`` /
        ``total``) which are resolved against the live Odoo catalog first
        (partner by name, product by SKU; never duplicates).

        ``CO:<intent_id>`` is stamped into ``client_order_ref`` and searched
        first; an existing order with the same marker is reported
        ``succeeded(replayed=True)`` (plan 二.4 read-before-create).
        """
        values = self._normalize_sale_order_values(values)
        values, marker = self._apply_marker(values, intent_id, field="client_order_ref")
        if marker:
            existing = self._search_one(
                "sale.order", [["client_order_ref", "=", marker]], ["id", "client_order_ref"]
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "sale.order", "create", {"vals_list": [values]}, operation="sale_order_create"
        )

    def confirm_sale_order(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Confirm a sales order; already-confirmed is idempotent success.

        Reads ``state`` first (when called through the WP5 seam with an
        ``intent_id``); ``sale``/``done`` mean the order is already at the
        target state (plan 二.4 state pre-check).
        """
        if intent_id is not None and self._read_state("sale.order", odoo_id) in {"sale", "done"}:
            return self._replayed_result(str(odoo_id), {"state": "sale"})
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
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a stock move idempotently (``origin=CO:<intent_id>``)."""
        values, marker = self._apply_marker(values, intent_id, field="origin")
        if marker:
            existing = self._search_one(
                "stock.move", [["origin", "=", marker]], ["id", "origin"]
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "stock.move", "create", {"vals_list": [values]}, operation="stock_move_create"
        )

    def _normalize_sale_order_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """Resolve business sale-order fields to Odoo-native values."""
        if "partner_id" in values or "order_line" in values:
            return values
        items = values.get("items") or []
        if not items:
            # No business fields: keep the raw values (marker replay path).
            return values
        partner = self._ensure_partner(str(values.get("partner_name") or "Shopify Customer"))
        order_lines = []
        for item in items:
            sku = str(item.get("sku") or "").strip()
            if not sku:
                raise ExternalSystemError(
                    "odoo.sale_order_create: line item missing 'sku'"
                )
            product = self._search_one(
                "product.template",
                [["default_code", "=", sku]],
                ["id", "default_code", "name"],
            )
            if product is None:
                raise ExternalSystemError(
                    f"odoo.sale_order_create: product with default_code {sku!r} not found"
                )
            qty = float(item.get("quantity") or item.get("qty") or 1)
            price = float(item.get("price") or values.get("total") or 0)
            order_lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": int(product["id"]),
                        "name": product.get("name") or sku,
                        "product_uom_qty": qty,
                        "price_unit": price,
                    },
                )
            )
        result: dict[str, Any] = {
            "partner_id": int(partner["id"]),
            "order_line": order_lines,
        }
        currency = str(values.get("currency") or "").strip()
        if currency:
            cur = self._search_one(
                "res.currency", [["name", "=", currency]], ["id", "name"]
            )
            if cur is not None:
                result["currency_id"] = int(cur["id"])
        return result

    def _ensure_partner(self, name: str) -> dict[str, Any]:
        """Find a res.partner by name or create it (sandbox helper)."""
        partner = self._search_one("res.partner", [["name", "=", name]], ["id", "name"])
        if partner is not None:
            return partner
        created = self._write(
            "res.partner",
            "create",
            {"vals_list": [{"name": name}]},
            operation="partner_create",
        )
        pid = created.remote_reference
        return {"id": pid, "name": name}
    def create_picking(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Resolve/create a picking idempotently.

        When ``sale_order_id`` is provided the picking is resolved from the
        confirmed sale order (``sale.order.action_confirm`` auto-creates it);
        otherwise the legacy marker path is kept (``origin=CO:<intent_id>``).
        """
        sale_order_id = values.get("sale_order_id")
        if sale_order_id:
            picking = self._search_one(
                "stock.picking",
                [["sale_id", "=", int(sale_order_id)]],
                ["id", "origin"],
            )
            if picking is None:
                raise ExternalSystemError(
                    f"odoo.picking_create: no picking for sale order {sale_order_id}"
                )
            return EffectResult.succeeded(
                remote_reference=str(picking["id"]),
                response_hash=payload_hash({"picking": picking}),
                replayed=False,
            )
        values, marker = self._apply_marker(values, intent_id, field="origin")
        if marker:
            existing = self._search_one(
                "stock.picking", [["origin", "=", marker]], ["id", "origin"]
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "stock.picking", "create", {"vals_list": [values]}, operation="picking_create"
        )

    def validate_picking(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Validate a picking; already done is idempotent success (seam)."""
        if intent_id is not None and self._read_state("stock.picking", odoo_id) == "done":
            return self._replayed_result(str(odoo_id), {"state": "done"})
        return self._write(
            "stock.picking",
            "button_validate",
            {"ids": [odoo_id]},
            operation="picking_validate",
            odoo_id=odoo_id,
        )

    def receive_transfer(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Validate an incoming transfer / receipt (``stock.picking`` ``button_validate``).

        ``odoo_id`` is the owning purchase order id: the incoming
        ``stock.picking`` generated by purchase_stock for that PO is resolved
        first (plan 二.4 read-before-act; no picking -> definitive failure,
        never a silent success).  Already-done pickings are reported
        ``succeeded(replayed=True)`` when the call carries an ``intent_id``.
        """
        picking = self._picking_for_po(int(odoo_id))
        if picking is None:
            raise ExternalSystemError(
                f"odoo.receive_transfer: no incoming stock.picking for PO {odoo_id}"
            )
        picking_id = int(picking["id"])
        odoo_id = picking_id
        if intent_id is not None and self._read_state("stock.picking", odoo_id) == "done":
            return self._replayed_result(str(odoo_id), {"state": "done"})
        return self._write(
            "stock.picking",
            "button_validate",
            {"ids": [odoo_id]},
            operation="receive_transfer",
            odoo_id=odoo_id,
        )

    def _picking_for_po(self, po_id: int) -> dict[str, Any] | None:
        """Resolve the incoming receipt picking auto-created for a PO."""
        pickings = self._search(
            "stock.picking",
            [["purchase_id", "=", int(po_id)]],
            ["id", "state", "origin"],
            limit=5,
        )
        if not pickings:
            return None
        # Prefer an in-progress incoming picking; fall back to any row.
        return next((p for p in pickings if p.get("state") != "done"), pickings[0])

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
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a customer invoice idempotently.

        When ``sale_order_id`` is provided the invoice is generated from the
        confirmed sale order (``sale.order.action_create_invoice``) and looked
        up by ``invoice_origin``; otherwise the legacy marker path is kept
        (``ref=CO:<intent_id>``).
        """
        sale_order_id = values.get("sale_order_id")
        order_ref = str(values.get("order_ref") or "")
        if sale_order_id:
            self._write(
                "sale.order",
                "action_create_invoice",
                {"ids": [int(sale_order_id)]},
                operation="invoice_create",
                odoo_id=int(sale_order_id),
            )
            if order_ref:
                move = self._search_one(
                    "account.move",
                    [
                        ["invoice_origin", "=", order_ref],
                        ["move_type", "=", "out_invoice"],
                    ],
                    ["id", "invoice_origin"],
                )
                if move is not None:
                    return EffectResult.succeeded(
                        remote_reference=str(move["id"]),
                        response_hash=payload_hash({"move": move}),
                        replayed=False,
                    )
            raise ExternalSystemError(
                f"odoo.invoice_create: no invoice for sale order {sale_order_id}"
            )
        body = dict(values)
        body.setdefault("move_type", "out_invoice")
        body, marker = self._apply_marker(body, intent_id, field="ref")
        if marker:
            existing = self._search_one(
                "account.move",
                [["ref", "=", marker], ["move_type", "=", "out_invoice"]],
                ["id", "ref"],
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "account.move", "create", {"vals_list": [body]}, operation="invoice_create"
        )

    def validate_invoice(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Post an invoice; already posted is idempotent success (seam)."""
        if intent_id is not None and self._read_state("account.move", odoo_id) == "posted":
            return self._replayed_result(str(odoo_id), {"state": "posted"})
        return self._write(
            "account.move",
            "action_post",
            {"ids": [odoo_id]},
            operation="invoice_validate",
            odoo_id=odoo_id,
        )

    def create_credit_note(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a credit note idempotently (``ref=CO:<intent_id>``)."""
        body = dict(values)
        body.setdefault("move_type", "out_refund")
        body, marker = self._apply_marker(body, intent_id, field="ref")
        if marker:
            existing = self._search_one(
                "account.move",
                [["ref", "=", marker], ["move_type", "=", "out_refund"]],
                ["id", "ref"],
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "account.move", "create", {"vals_list": [body]}, operation="credit_note_create"
        )

    def validate_credit_note(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Post a credit note; already posted is idempotent success (seam)."""
        if intent_id is not None and self._read_state("account.move", odoo_id) == "posted":
            return self._replayed_result(str(odoo_id), {"state": "posted"})
        return self._write(
            "account.move",
            "action_post",
            {"ids": [odoo_id]},
            operation="credit_note_validate",
            odoo_id=odoo_id,
        )

    def create_po(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a PO idempotently (``partner_ref=CO:<intent_id>``).

        Accepts either Odoo-native ``purchase.order`` values (``partner_id`` +
        ``order_line``) or the domain business fields (``sku`` / ``supplier`` /
        ``qty`` / ``uom`` / ``unit_cost`` / ``currency``) which are resolved
        against the live Odoo catalog first (plan 二.4: PO create queries
        product by SKU / partner by supplier before creating, never duplicates).
        """
        values, marker = self._apply_marker(values, intent_id, field="partner_ref")
        values = self._normalize_po_values(values)
        if marker:
            existing = self._search_one(
                "purchase.order", [["partner_ref", "=", marker]], ["id", "partner_ref"]
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write(
            "purchase.order", "create", {"vals_list": [values]}, operation="po_create"
        )

    def _normalize_po_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """Resolve business PO fields to Odoo-native purchase.order values."""
        if "partner_id" in values or "order_line" in values:
            return values
        sku = str(values.get("sku") or "").strip()
        supplier = str(values.get("supplier") or "").strip()
        if not sku or not supplier:
            raise ExternalSystemError(
                "odoo.po_create: business values require 'sku' and 'supplier'"
            )
        product = self._search_one(
            "product.template",
            [["default_code", "=", sku]],
            ["id", "default_code", "name"],
        )
        if product is None:
            raise ExternalSystemError(
                f"odoo.po_create: product with default_code {sku!r} not found"
            )
        partner = self._search_one(
            "res.partner",
            [["name", "=", supplier]],
            ["id", "name"],
        )
        if partner is None:
            raise ExternalSystemError(f"odoo.po_create: partner {supplier!r} not found")
        qty = float(values.get("qty") or 1)
        unit_cost = float(values.get("unit_cost") or 0)
        return {
            "partner_id": int(partner["id"]),
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": int(product["id"]),
                        "name": product.get("name") or sku,
                        "product_qty": qty,
                        "price_unit": unit_cost,
                    },
                )
            ],
            "partner_ref": values.get("partner_ref"),
        }

    def confirm_po(
        self,
        odoo_id: int,
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Confirm a PO; already confirmed is idempotent success (seam)."""
        if intent_id is not None and self._read_state("purchase.order", odoo_id) in {
            "confirmed",
            "done",
        }:
            return self._replayed_result(str(odoo_id), {"state": "confirmed"})
        return self._write(
            "purchase.order",
            "button_confirm",
            {"ids": [odoo_id]},
            operation="po_confirm",
            odoo_id=odoo_id,
        )

    def create_bill(
        self,
        values: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id=None,
    ) -> EffectResult:
        """Create a vendor bill idempotently (``ref=CO:<intent_id>``).

        Accepts either Odoo-native ``account.move`` values or the domain
        business fields (``sku`` / ``supplier`` / ``qty`` / ``unit_cost`` /
        ``po_id``), resolved against the live Odoo catalog first (plan 二.4).
        """
        body = dict(values)
        body = self._normalize_bill_values(body)
        body.setdefault("move_type", "in_invoice")
        body, marker = self._apply_marker(body, intent_id, field="ref")
        if marker:
            existing = self._search_one(
                "account.move",
                [["ref", "=", marker], ["move_type", "=", "in_invoice"]],
                ["id", "ref"],
            )
            if existing:
                return self._replayed_result(str(existing["id"]), existing)
        return self._write("account.move", "create", {"vals_list": [body]}, operation="bill_create")

    def _normalize_bill_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """Resolve business bill fields to Odoo-native account.move values."""
        if "partner_id" in values or "invoice_line_ids" in values:
            return values
        sku = str(values.get("sku") or "").strip()
        supplier = str(values.get("supplier") or "").strip()
        if not sku or not supplier:
            raise ExternalSystemError(
                "odoo.bill_create: business values require 'sku' and 'supplier'"
            )
        product = self._search_one(
            "product.template",
            [["default_code", "=", sku]],
            ["id", "default_code", "name"],
        )
        if product is None:
            raise ExternalSystemError(
                f"odoo.bill_create: product with default_code {sku!r} not found"
            )
        partner = self._search_one(
            "res.partner",
            [["name", "=", supplier]],
            ["id", "name"],
        )
        if partner is None:
            raise ExternalSystemError(f"odoo.bill_create: partner {supplier!r} not found")
        qty = float(values.get("qty") or 1)
        unit_cost = float(values.get("unit_cost") or 0)
        body: dict[str, Any] = {
            "partner_id": int(partner["id"]),
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "product_id": int(product["id"]),
                        "name": product.get("name") or sku,
                        "quantity": qty,
                        "price_unit": unit_cost,
                    },
                )
            ],
        }
        if values.get("ref"):
            body["ref"] = values["ref"]
        return body

    # ------------------------------------------------------------------ #
    # Reconciliation reads (read-only, never state-changing)
    # ------------------------------------------------------------------ #

    def read_records(
        self,
        model: str,
        ids: list[int],
        fields: list[str],
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Read Odoo records by id (effect-domain read-back)."""
        if not ids:
            return []
        return self._search(
            model, [["id", "in", [int(i) for i in ids]]], fields, limit=min(limit, 500)
        )

    def list_products(
        self, *, offset: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """List product templates for the catalog reconciliation domain."""
        return self._search(
            "product.template",
            [],
            ["id", "default_code", "name", "type", "categ_id"],
            limit=min(limit, 500),
            offset=offset,
        )

    def list_sale_orders(
        self, *, offset: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """List sale orders for the order reconciliation domain."""
        return self._search(
            "sale.order",
            [["state", "!=", "cancel"]],
            ["id", "name", "client_order_ref", "amount_total", "currency_id", "state"],
            limit=min(limit, 500),
            offset=offset,
        )

    def list_purchase_orders(
        self, *, offset: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """List purchase orders for the procurement reconciliation domain."""
        return self._search(
            "purchase.order",
            [["state", "!=", "cancel"]],
            ["id", "name", "partner_ref", "amount_total", "currency_id", "state"],
            limit=min(limit, 500),
            offset=offset,
        )

    def list_account_moves(
        self, move_type: str, *, offset: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        """List account moves of one type (invoice/bill/credit note)."""
        return self._search(
            "account.move",
            [["move_type", "=", move_type]],
            ["id", "name", "ref", "amount_total", "currency_id", "state", "move_type"],
            limit=min(limit, 500),
            offset=offset,
        )

    def list_quants(self, *, offset: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        """List stock quants for the inventory reconciliation domain."""
        return self._search(
            "stock.quant",
            [["quantity", "!=", 0.0]],
            ["id", "product_id", "location_id", "quantity"],
            limit=min(limit, 500),
            offset=offset,
        )


__all__ = ["OdooApiError", "OdooConnector"]
