"""Shopify Admin GraphQL connector (dev store).

Contract (ADR-0007):

- **Pinned API version** from settings (``COMMERCE_SHOPIFY_API_VERSION``,
  e.g. ``2026-07``); ``latest`` is never used. Endpoint:
  ``https://{shop}.myshopify.com/admin/api/{version}/graphql.json``.
- **Auth**: ``X-Shopify-Access-Token`` request header.
- **Sync by default** (see ``app.connectors.base``): a sync ``httpx.Client``
  with a bounded timeout. An async variant is intentionally not shipped in
  v1; it can be added as a thin wrapper later without changing the method
  contracts.
- **Triple error check** on every mutation: HTTP status, top-level GraphQL
  ``errors``, and the requested mutation's ``userErrors``. Any of them
  failing means the effect did not succeed.
- **Idempotency**: ``refundCreate`` supports native idempotency via the
  ``@idempotent(key:)`` directive (required since API 2026-04). The other
  mutations have no real idempotency; every operation accepts an
  ``idempotency_key`` for the caller-level effect ledger (ADR-0004) and
  attaches it to logs/traces.

Timeout/transport and 5xx failures raise :class:`OutcomeUnknownError` (never
blind-retried); definitive 4xx failures raise
:class:`app.core.errors.ExternalSystemError`; expected conflicts return
``EffectResult(ok=False, status="failed")``.
"""

from __future__ import annotations

import json
import ssl
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import certifi
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

logger = get_logger("commerce.connectors.shopify")

# 本机 v2rayN TUN 的 IPv6 出口异常（环境运维手册记录），IPv4 直连稳定：
# 连接器进程内 DNS 解析优先 IPv4。
prefer_ipv4()

# certifi 包 + Windows 系统证书库合并信任：直连路径偶发仅下发不完整链，
# 系统库缓存了中间证书，合并后校验最稳（本机实测）。
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
with suppress(Exception):  # pragma: no cover - Windows 以外平台无系统库可加载
    _SSL_CONTEXT.load_default_certs(ssl.Purpose.SERVER_AUTH)

_TOKEN_EXCHANGE_MAX_ATTEMPTS = 5
_TOKEN_EXCHANGE_BACKOFF = 1.0  # seconds; sleeps 1s, 2s, 3s, 4s between attempts

_CONFLICT_MARKERS = (
    "has already been taken",
    "already exists",
    "already in use",
    "already published",
)
"""Best-effort userErrors substrings that indicate an expected conflict.

Anything not matching these markers is raised as ExternalSystemError; the
workflow decides how to treat a ``failed`` conflict result.
"""

_OPEN_FULFILLMENT_ORDER_STATUSES = {"OPEN", "IN_PROGRESS"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _payload_matches_product(payload: dict[str, Any], product: dict[str, Any]) -> bool:
    """True when every top-level ``payload`` field already equals ``product``.

    Values are compared as strings so Shopify-side type normalization does not
    produce false drift (used by the read-back idempotency strategy: target
    state already present == success).
    """
    for key, expected in payload.items():
        actual = product.get(key)
        if actual is None and expected is None:
            continue
        if actual is None or str(actual) != str(expected):
            return False
    return True


class ShopifyConnector:
    """Synchronous Shopify Admin GraphQL connector.

    Construction reads settings and performs no network I/O; the
    ``httpx.Client`` is created lazily on first request. Pass ``client`` to
    inject a mock/recorded client in tests.
    """

    name = "shopify"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.shop_name = self.settings.shopify_shop_name.strip()
        self.access_token = self.settings.shopify_access_token.strip()
        self.client_id = self.settings.shopify_client_id.strip()
        self.client_secret = self.settings.shopify_client_secret.strip()
        self.api_version = self.settings.shopify_api_version.strip() or "2026-07"
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._cached_token: str | None = None
        self._token_expires_at: float | None = None

    # ------------------------------------------------------------------ #
    # Configuration and HTTP plumbing
    # ------------------------------------------------------------------ #

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop_name}.myshopify.com/admin/api/{self.api_version}/graphql.json"

    @property
    def versions_endpoint(self) -> str:
        return f"https://{self.shop_name}.myshopify.com/admin/api/versions.json"

    def _require_configured(self) -> None:
        if not self.shop_name or not self._has_credentials():
            raise ConnectorError(
                "Shopify connector is not configured: set COMMERCE_SHOPIFY_SHOP_NAME "
                "and (COMMERCE_SHOPIFY_ACCESS_TOKEN or COMMERCE_SHOPIFY_CLIENT_ID + "
                "COMMERCE_SHOPIFY_CLIENT_SECRET)"
            )

    def _has_credentials(self) -> bool:
        return bool(self.access_token or (self.client_id and self.client_secret))

    def exchange_client_credentials_token(self) -> str:
        """Exchange Client ID + Client Secret for a ~24h Admin API access token.

        Client-credentials grant (Dev Dashboard apps):
        ``POST https://{shop}.myshopify.com/admin/oauth/access_token`` with
        ``grant_type=client_credentials``. The returned token is an Admin API
        access token usable with ``X-Shopify-Access-Token`` (expires_in 86399s).

        Bounded retry: the exchange is a side-effect-free credential fetch.
        The local egress (v2rayN TUN) can transiently fail TLS (see
        environment ops manual: single-endpoint timeouts are not proxy
        failure); retrying an idempotent token request is safe and distinct
        from re-dispatching a business effect.
        """
        if not (self.shop_name and self.client_id and self.client_secret):
            raise ConnectorError(
                "Shopify client-credentials exchange requires COMMERCE_SHOPIFY_CLIENT_ID "
                "and COMMERCE_SHOPIFY_CLIENT_SECRET"
            )
        url = f"https://{self.shop_name}.myshopify.com/admin/oauth/access_token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        last_error: Exception | None = None
        for attempt in range(1, _TOKEN_EXCHANGE_MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=self.timeout,
                    # 直连 Shopify：系统代理只用于 GitHub 等被阻断域名（AGENTS.md），
                    # 误走代理会 TLS 握手超时。
                    trust_env=False,
                    verify=_SSL_CONTEXT,
                )
                break
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < _TOKEN_EXCHANGE_MAX_ATTEMPTS:
                    logger.warning(
                        "shopify_token_exchange_retry",
                        attempt=attempt,
                        error_type=type(exc).__name__,
                        error=str(exc)[:160],
                    )
                    time.sleep(_TOKEN_EXCHANGE_BACKOFF * attempt)
        else:
            assert last_error is not None
            raise OutcomeUnknownError(
                "Shopify client-credentials exchange failed after "
                f"{_TOKEN_EXCHANGE_MAX_ATTEMPTS} attempts with transport error "
                f"({type(last_error).__name__}); outcome unknown — do not blind-retry"
            ) from last_error
        if response.status_code != 200:
            raise ExternalSystemError(
                f"Shopify client-credentials exchange failed (HTTP "
                f"{response.status_code}): {truncate(response.text, 400)}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ExternalSystemError(
                "Shopify client-credentials exchange returned non-JSON body"
            ) from exc
        token = payload.get("access_token")
        if not token:
            raise ExternalSystemError("Shopify client-credentials response missing access_token")
        return str(token)

    def _resolve_access_token(self) -> str:
        """Return a usable Admin API access token (client-credentials preferred).

        The Dev Dashboard client-credentials grant is the documented auth path
        for this deployment (ADR-0007); the static
        ``COMMERCE_SHOPIFY_ACCESS_TOKEN`` is only a fallback for setups
        without client credentials.  When both are configured, prefer the
        exchange so a stale static token cannot silently break every call.
        """
        if self.client_id and self.client_secret:
            now = time.time()
            if self._cached_token and self._token_expires_at and now < self._token_expires_at:
                return self._cached_token
            token = self.exchange_client_credentials_token()
            self._cached_token = token
            # expires_in is 86399s; refresh an hour early.
            self._token_expires_at = now + (86399 - 3600)
            logger.info("shopify_client_credentials_token_exchanged")
            return token
        if self.access_token:
            return self.access_token
        raise ConnectorError(
            "Shopify connector has no usable access token: set "
            "COMMERCE_SHOPIFY_ACCESS_TOKEN or COMMERCE_SHOPIFY_CLIENT_ID + "
            "COMMERCE_SHOPIFY_CLIENT_SECRET"
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers=self._request_headers(),
                trust_env=False,
                verify=_SSL_CONTEXT,
            )
        return self._client

    def _request_headers(self) -> dict[str, str]:
        return {
            "X-Shopify-Access-Token": self._resolve_access_token(),
            "User-Agent": f"commerce-orchestrator/{APP_VERSION}",
        }

    def close(self) -> None:
        """Close the owned HTTP client (no-op for injected clients)."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _graphql(self, query: str, variables: dict[str, Any], *, operation: str) -> dict[str, Any]:
        """POST a GraphQL document and return the parsed payload.

        Raises OutcomeUnknownError for ambiguous transport/5xx failures and
        ExternalSystemError for definitive HTTP failures or malformed JSON.
        """
        self._require_configured()
        tracer = get_tracer()
        with tracer.start_as_current_span(f"connector.shopify.{operation}") as span:
            span.set_attribute("shopify.operation", operation)
            span.set_attribute("shopify.api_version", self.api_version)
            try:
                response = self._get_client().post(
                    self.endpoint,
                    json={"query": query, "variables": variables},
                    headers=self._request_headers(),
                )
            except httpx.TransportError as exc:
                span.record_exception(exc)
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport error"
                logger.warning(
                    "shopify_request_outcome_unknown",
                    operation=operation,
                    error_type=kind,
                    error=str(exc),
                )
                raise OutcomeUnknownError(
                    f"Shopify {operation} failed with {kind} ({type(exc).__name__}); "
                    "outcome unknown — do not blind-retry, route to reconciliation"
                ) from exc
            payload = self._decode_response(response, operation=operation)
            if payload.get("errors"):
                detail = "; ".join(str(e.get("message", e)) for e in payload["errors"])
                raise ExternalSystemError(
                    f"Shopify GraphQL top-level errors on {operation}: {truncate(detail)}"
                )
            return payload

    def _decode_response(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        """Classify the HTTP response into a payload or a raised error."""
        status = response.status_code
        if 200 <= status < 300:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ExternalSystemError(
                    f"Shopify {operation} returned HTTP {status} with non-JSON body"
                ) from exc
            if not isinstance(payload, dict):
                raise ExternalSystemError(
                    f"Shopify {operation} returned unexpected JSON shape (expected object)"
                )
            return payload

        body = truncate(response.text, 800)
        if status in (401, 403):
            raise ExternalSystemError(
                f"Shopify {operation} authentication/authorization failed (HTTP {status}): {body}"
            )
        if status == 404:
            raise ExternalSystemError(f"Shopify {operation} not found (HTTP 404): {body}")
        if status == 429:
            # 429 is returned before the mutation is processed: the effect was
            # definitively NOT applied, so retrying the same intent is safe
            # (maps to EffectFailed(retryable=True), max 3 attempts).
            raise RetryableEffectError(
                f"Shopify {operation} rate limited (HTTP 429) before processing; "
                f"the effect was not applied — retry is safe: {body}"
            )
        if status == 408 or status >= 500:
            logger.warning("shopify_request_outcome_unknown", operation=operation, status=status)
            raise OutcomeUnknownError(
                f"Shopify {operation} returned HTTP {status}; the mutation may have been "
                "applied before the failure — outcome unknown, route to reconciliation"
            )
        raise ExternalSystemError(f"Shopify {operation} failed (HTTP {status}): {body}")

    def _mutation(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        operation: str,
        response_key: str,
        reference: Callable[[dict[str, Any]], str | None],
    ) -> EffectResult:
        """Run a mutation with the triple error check (ADR-0007)."""
        payload = self._graphql(query, variables, operation=operation)
        result = (payload.get("data") or {}).get(response_key)
        if result is None:
            raise ExternalSystemError(
                f"Shopify {operation} response missing '{response_key}' payload"
            )
        user_errors = result.get("userErrors") or []
        if user_errors:
            detail = "; ".join(
                f"{e.get('field', '?')}: {e.get('message', str(e))}" for e in user_errors
            )
            lowered = detail.lower()
            if any(marker in lowered for marker in _CONFLICT_MARKERS):
                logger.info(
                    "shopify_expected_conflict", operation=operation, detail=truncate(detail)
                )
                return EffectResult.failed(
                    error=f"Shopify {operation} conflict: {detail}",
                    response_hash=payload_hash(payload),
                )
            raise ExternalSystemError(f"Shopify {operation} userErrors: {truncate(detail)}")
        try:
            remote_reference = reference(result)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExternalSystemError(
                f"Shopify {operation} response missing expected reference field: {exc}"
            ) from exc
        logger.info(
            "shopify_effect_succeeded",
            operation=operation,
            remote_reference=remote_reference,
        )
        return EffectResult.succeeded(
            remote_reference=remote_reference, response_hash=payload_hash(payload)
        )

    # ------------------------------------------------------------------ #
    # Connectivity / auth probe (read-only)
    # ------------------------------------------------------------------ #

    def probe(self) -> dict[str, Any]:
        """Check connectivity and auth against the pinned Admin API version.

        Runs a minimal read-only GraphQL query (``shop { name }``) against
        the pinned API version. Never raises; returns:
        ``{"ok": bool, "api_version": str, "installed": bool}``.
        """
        if not self.shop_name or not self._has_credentials():
            return {
                "ok": False,
                "api_version": self.api_version,
                "installed": False,
            }
        try:
            payload = self._graphql("query { shop { name } }", {}, operation="probe")
        except (OutcomeUnknownError, ExternalSystemError) as exc:
            logger.warning("shopify_probe_failed", error=exc.detail)
            return {
                "ok": False,
                "api_version": self.api_version,
                "installed": False,
            }
        shop = (payload.get("data") or {}).get("shop")
        return {
            "ok": bool(shop),
            "api_version": self.api_version,
            "installed": bool(shop),
        }

    # ------------------------------------------------------------------ #
    # Product operations
    # ------------------------------------------------------------------ #

    def create_product(
        self, payload: dict[str, Any], *, idempotency_key: str | None = None
    ) -> EffectResult:
        """Create a product (``productCreate``).

        ``payload`` maps to ``ProductCreateInput`` (``title`` is required).
        The returned reference is the product GID.
        """
        if not payload.get("title"):
            raise ConnectorError("Shopify create_product requires payload['title']")
        query = """
            mutation productCreate($product: ProductCreateInput!) {
              productCreate(product: $product) {
                product { id handle }
                userErrors { field message }
              }
            }
        """
        variables: dict[str, Any] = {"product": payload}
        return self._mutation(
            query,
            variables,
            operation="product_create",
            response_key="productCreate",
            reference=lambda result: result["product"]["id"],
        )

    def update_product(
        self,
        gid: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        intent_id: str | None = None,
    ) -> EffectResult:
        """Update a product by GID with read-back idempotency.

        Reads the product first; when every top-level field in ``payload``
        already matches the remote product, the update is skipped and the
        result is reported ``succeeded(replayed=True)`` — the target state
        already exists. Otherwise ``productUpdate`` runs; ``idempotency_key``
        is logged for caller-level ledger correlation (no native idempotency).
        """
        existing = self.get_product(gid)
        if existing is not None and _payload_matches_product(payload, existing):
            logger.info("shopify_product_update_replayed", gid=gid, intent_id=intent_id)
            return EffectResult.succeeded(
                remote_reference=gid,
                response_hash=payload_hash({"product": existing}),
                replayed=True,
            )
        query = """
            mutation productUpdate(
              $identifier: ProductUpdateIdentifiers!
              $product: ProductUpdateInput!
            ) {
              productUpdate(identifier: $identifier, product: $product) {
                product { id }
                userErrors { field message }
              }
            }
        """
        variables = {"identifier": {"id": gid}, "product": payload}
        return self._mutation(
            query,
            variables,
            operation="product_update",
            response_key="productUpdate",
            reference=lambda result: result.get("product", {}).get("id") or gid,
        )

    def bulk_update_variants(
        self,
        product_gid: str,
        variants: list[dict[str, Any]],
        *,
        idempotency_key: str | None = None,
    ) -> EffectResult:
        """Update product variants (``productVariantsBulkUpdate``, 2026-07).

        Since API 2026-07, ``ProductUpdateInput`` no longer accepts
        ``variants``; variant fields (sku, price, inventoryQuantity...) are
        updated via ``productVariantsBulkUpdate`` with ``variantUpdates``.
        """
        query = """
            mutation productVariantsBulkUpdate(
              $productId: ID!
              $variants: [ProductVariantsBulkInput!]!
            ) {
              productVariantsBulkUpdate(productId: $productId, variants: $variants) {
                productVariants { id sku price }
                userErrors { field message }
              }
            }
        """
        return self._mutation(
            query,
            {"productId": product_gid, "variants": variants},
            operation="product_variants_bulk_update",
            response_key="productVariantsBulkUpdate",
            reference=lambda result: (
                (result.get("productVariants") or [{}])[0].get("id")
                if result.get("productVariants")
                else None
            ),
        )

    def publish_product(
        self,
        gid: str,
        *,
        publication_id: str | None = None,
        publish_date: str | None = None,
        idempotency_key: str | None = None,
        intent_id: str | None = None,
    ) -> EffectResult:
        """Publish a product with read-back idempotency.

        ``input`` is a ``[PublicationInput!]!`` list; with no
        ``publication_id`` the default (Online Store) publication is used.
        ``publish_date`` defaults to now; a future date schedules publishing.
        When the product is already published to the target publication, the
        publish is skipped and reported ``succeeded(replayed=True)``.
        """
        status = self.get_product_publish_status(gid)
        if status is not None and status["isPublished"] and (
            publication_id is None or publication_id in status["publicationIds"]
        ):
            logger.info(
                "shopify_product_publish_replayed",
                gid=gid,
                publication_id=publication_id,
                intent_id=intent_id,
            )
            return EffectResult.succeeded(
                remote_reference=gid, response_hash=payload_hash(status), replayed=True
            )
        publication: dict[str, Any] = {"publishDate": publish_date or _utc_now_iso()}
        if publication_id:
            publication["publicationId"] = publication_id
        query = """
            mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
              publishablePublish(id: $id, input: $input) {
                publishable { ... on Product { id } }
                userErrors { field message }
              }
            }
        """
        return self._mutation(
            query,
            {"id": gid, "input": [publication]},
            operation="product_publish",
            response_key="publishablePublish",
            reference=lambda result: (result.get("publishable") or {}).get("id") or gid,
        )

    # ------------------------------------------------------------------ #
    # Fulfillment and refunds
    # ------------------------------------------------------------------ #

    def create_fulfillment(
        self,
        order_gid: str,
        tracking: dict[str, Any] | None,
        location_gid: str | None = None,
        *,
        fulfillment_order_gid: str | None = None,
        notify_customer: bool = False,
        idempotency_key: str | None = None,
        intent_id: str | None = None,
    ) -> EffectResult:
        """Fulfill a whole order (``fulfillmentCreate``), pre-checked.

        2026-07 fulfills via fulfillment orders, so when
        ``fulfillment_order_gid`` is omitted the connector resolves the
        order's first open fulfillment order (read query) and fulfills all
        of its line items. ``location_gid`` is accepted for v1 signature
        compatibility; in 2026-07 the fulfillment order's assigned location
        is authoritative (a provided gid is logged, not asserted).

        Idempotency strategy: the order's fulfillment orders **and existing
        fulfillments** are read first. An existing fulfillment means the
        effect already happened -> ``succeeded(replayed=True)``. A read
        failure (transport/5xx) raises ``OutcomeUnknownError`` so the caller
        routes the effect to reconciliation instead of guessing.

        ``tracking``: ``{"company"?, "number"?, "url"?}``.
        """
        state = self._read_fulfillment_state(order_gid)
        if state is None:
            return EffectResult.failed(
                error=f"Shopify fulfillment_create: order {order_gid} not found"
            )
        if state["fulfillments"]:
            existing = state["fulfillments"][0]
            logger.info(
                "shopify_fulfillment_replayed",
                order_gid=order_gid,
                fulfillment_gid=existing.get("id"),
                intent_id=intent_id,
            )
            return EffectResult.succeeded(
                remote_reference=existing.get("id"),
                response_hash=payload_hash(state),
                replayed=True,
            )
        if fulfillment_order_gid is None:
            fulfillment_order_gid = self._pick_fulfillment_order(
                state["fulfillment_orders"], preferred_location_gid=location_gid
            )
            if fulfillment_order_gid is None:
                return EffectResult.failed(
                    error=(
                        f"Shopify fulfillment_create: no open fulfillment order for "
                        f"order {order_gid}"
                    )
                )
        tracking_input: dict[str, Any] | None = None
        if tracking:
            tracking_input = {k: v for k, v in tracking.items() if v is not None}
        fulfillment: dict[str, Any] = {
            "lineItemsByFulfillmentOrder": [
                {
                    "fulfillmentOrderId": fulfillment_order_gid,
                    "fulfillmentOrderLineItems": [],
                }
            ],
            "notifyCustomer": notify_customer,
        }
        if tracking_input:
            fulfillment["trackingInfo"] = tracking_input
        query = """
            mutation fulfillmentCreate($fulfillment: FulfillmentInput!) {
              fulfillmentCreate(fulfillment: $fulfillment) {
                fulfillment { id status }
                userErrors { field message }
              }
            }
        """
        return self._mutation(
            query,
            {"fulfillment": fulfillment},
            operation="fulfillment_create",
            response_key="fulfillmentCreate",
            reference=lambda result: (result.get("fulfillment") or {}).get("id"),
        )

    def _resolve_fulfillment_order(
        self, order_gid: str, *, preferred_location_gid: str | None = None
    ) -> str | None:
        """Return the first open fulfillment order GID for an order."""
        state = self._read_fulfillment_state(order_gid)
        if state is None:
            return None
        return self._pick_fulfillment_order(
            state["fulfillment_orders"], preferred_location_gid=preferred_location_gid
        )

    def _read_fulfillment_state(self, order_gid: str) -> dict[str, Any] | None:
        """Read an order's fulfillment orders + existing fulfillments.

        Returns ``None`` when the order does not exist; otherwise
        ``{"order_gid", "fulfillments": [...], "fulfillment_orders": [...]}``.
        Transport/5xx failures raise :class:`OutcomeUnknownError` (outcome
        ambiguous — route to reconciliation).
        """
        query = """
            query orderFulfillmentState($id: ID!) {
              order(id: $id) {
                id
                fulfillments {
                  id
                  status
                }
                fulfillmentOrders(first: 10) {
                  edges {
                    node {
                      id
                      status
                      assignedLocation {
                        location { id }
                      }
                    }
                  }
                }
              }
            }
        """
        payload = self._graphql(query, {"id": order_gid}, operation="read_fulfillment_state")
        order = (payload.get("data") or {}).get("order")
        if order is None:
            return None
        fulfillments = [n for n in (order.get("fulfillments") or []) if n]
        fulfillment_orders = [
            edge.get("node") or {}
            for edge in (order.get("fulfillmentOrders") or {}).get("edges") or []
            if edge.get("node")
        ]
        return {
            "order_gid": order_gid,
            "fulfillments": fulfillments,
            "fulfillment_orders": fulfillment_orders,
        }

    def _pick_fulfillment_order(
        self,
        fulfillment_orders: list[dict[str, Any]],
        *,
        preferred_location_gid: str | None = None,
    ) -> str | None:
        """Return the first open fulfillment order GID from a node list."""
        for node in fulfillment_orders:
            if node.get("status") in _OPEN_FULFILLMENT_ORDER_STATUSES:
                location_id = node.get("assignedLocation", {}).get("location", {}).get("id")
                if preferred_location_gid and location_id != preferred_location_gid:
                    logger.info(
                        "shopify_fulfillment_order_location_mismatch",
                        preferred_location_gid=preferred_location_gid,
                        actual_location_gid=location_id,
                    )
                    continue
                return node.get("id")
        return None

    def create_refund(
        self,
        order_gid: str,
        amount: str | int | float,
        *,
        note: str | None = None,
        notify: bool = False,
        refund_line_items: list[dict[str, Any]] | None = None,
        parent_transaction_id: str | None = None,
        gateway: str = "manual",
        idempotency_key: str | None = None,
        allow_real_money: bool = False,
    ) -> EffectResult:
        """Create a refund (``refundCreate``, dev store only).

        Safety rail: refuses to run unless ``allow_real_money=True`` is
        passed explicitly. Native idempotency is REQUIRED by the API since
        2026-04 (``@idempotent(key:)``), so ``idempotency_key`` must be
        provided. The transaction is created against the ``manual`` gateway
        so no real payment is ever moved.
        """
        if not allow_real_money:
            raise ConnectorError(
                "Shopify create_refund is dev-store-only and never moves real money; "
                "pass allow_real_money=True explicitly to proceed"
            )
        if not idempotency_key:
            raise ConnectorError(
                "Shopify refundCreate requires idempotency_key (native @idempotent "
                "directive, required since API 2026-04)"
            )
        transaction: dict[str, Any] = {
            "amount": str(amount),
            "kind": "REFUND",
            "gateway": gateway,
            "orderId": order_gid,
        }
        if parent_transaction_id:
            # 非 store-credit/exchange/cash 网关的退款事务必须带父支付交易。
            transaction["parentId"] = parent_transaction_id
        refund_input: dict[str, Any] = {
            "orderId": order_gid,
            "notify": notify,
            "transactions": [transaction],
        }
        if note:
            refund_input["note"] = note
        if refund_line_items:
            refund_input["refundLineItems"] = refund_line_items
        query = """
            mutation refundCreate($input: RefundInput!, $key: String!) {
              refundCreate(input: $input) @idempotent(key: $key) {
                refund { id }
                userErrors { field message }
              }
            }
        """
        return self._mutation(
            query,
            {"input": refund_input, "key": idempotency_key},
            operation="refund_create",
            response_key="refundCreate",
            reference=lambda result: (result.get("refund") or {}).get("id"),
        )

    # ------------------------------------------------------------------ #
    # Read queries (reconciliation and inventory projections)
    # ------------------------------------------------------------------ #

    def list_orders(
        self,
        updated_after: str | None,
        first: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Incrementally list orders updated after an ISO timestamp.

        Cursor-paginated ``orders`` query sorted by ``UPDATED_AT``; returns
        ``(nodes, next_cursor)`` where ``next_cursor`` is ``None`` when the
        page is complete. Used for updated_at-based reconciliation.
        """
        if updated_after:
            query = """
            query ordersPageFiltered(
              $after: String
              $first: Int!
              $query: String!
            ) {
              orders(first: $first, after: $after, sortKey: UPDATED_AT, query: $query) {
                edges {
                  node {
                    id
                    legacyResourceId
                    name
                    updatedAt
                    createdAt
                    displayFinancialStatus
                    displayFulfillmentStatus
                    totalPriceSet {
                      presentmentMoney { amount currencyCode }
                    }
                    lineItems(first: 100) {
                      edges { node { id title sku quantity } }
                    }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
            """
            variables = {
                "after": cursor,
                "first": first,
                "query": f"updated_at:>'{updated_after}'",
            }
        else:
            query = """
            query ordersPage($after: String, $first: Int!) {
              orders(first: $first, after: $after, sortKey: UPDATED_AT) {
                edges {
                  node {
                    id
                    legacyResourceId
                    name
                    updatedAt
                    createdAt
                    displayFinancialStatus
                    displayFulfillmentStatus
                    totalPriceSet {
                      presentmentMoney { amount currencyCode }
                    }
                    lineItems(first: 100) {
                      edges { node { id title sku quantity } }
                    }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
            """
            variables = {"after": cursor, "first": first}
        payload = self._graphql(query, variables, operation="list_orders")
        data = payload.get("data") or {}
        if updated_after:
            edges = (data.get("orders") or {}).get("edges") or []
            nodes = [edge["node"] for edge in edges if edge.get("node")]
            page_info = (data.get("orders") or {}).get("pageInfo") or {}
        else:
            edges = (data.get("orders") or {}).get("edges") or []
            nodes = [edge["node"] for edge in edges if edge.get("node")]
            page_info = (data.get("orders") or {}).get("pageInfo") or {}
        next_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
        return nodes, next_cursor

    def list_inventory(
        self, first: int = 5, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List variant inventory levels with cursor pagination (2026-07).

        Since API 2026-07 the top-level ``inventoryLevels`` query was
        removed; inventory is read through ``product -> variants ->
        inventoryItem.inventoryLevels``.  ``available`` is read from the
        ``quantities(names: ["available"])`` list (the legacy
        ``InventoryLevel.available`` field no longer exists).  Returns
        ``(nodes, next_cursor)``; each node carries the variant GID, SKU,
        inventory item / level / location ids and the available quantity.

        ``first`` bounds the products page (clamped to 1..5) so the nested
        ``variants -> inventoryItem -> inventoryLevels`` fan-out stays under
        the single-query cost limit; larger stores are read with the cursor.
        """
        page = max(1, min(int(first), 5))
        query = """
            query inventoryLevelsPage($after: String, $first: Int!) {
              products(first: $first, after: $after) {
                edges {
                  node {
                    id
                    variants(first: 10) {
                      edges {
                        node {
                          id
                          sku
                          inventoryItem {
                            id
                            sku
                            inventoryLevels(first: 5) {
                              edges {
                                node {
                                  id
                                  location { id }
                                  quantities(names: ["available"]) { name quantity }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
        """
        payload = self._graphql(query, {"after": cursor, "first": page}, operation="list_inventory")
        data = payload.get("data") or {}
        edges = (data.get("products") or {}).get("edges") or []
        page_info = (data.get("products") or {}).get("pageInfo") or {}
        nodes: list[dict[str, Any]] = []
        for edge in edges:
            product = edge.get("node") or {}
            for variant_edge in (product.get("variants") or {}).get("edges") or []:
                variant = variant_edge.get("node") or {}
                inventory_item = variant.get("inventoryItem") or {}
                for level_edge in (inventory_item.get("inventoryLevels") or {}).get("edges") or []:
                    level = level_edge.get("node") or {}
                    available: int | None = None
                    for q in level.get("quantities") or []:
                        if q.get("name") == "available":
                            available = q.get("quantity")
                    nodes.append(
                        {
                            "id": variant.get("id"),
                            "variant_id": variant.get("id"),
                            "sku": variant.get("sku") or inventory_item.get("sku"),
                            "inventory_item_id": inventory_item.get("id"),
                            "inventory_level_id": level.get("id"),
                            "location_id": (level.get("location") or {}).get("id"),
                            "available": available,
                            "quantities": level.get("quantities") or [],
                        }
                    )
        next_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
        return nodes, next_cursor

    def get_product(self, gid: str) -> dict[str, Any] | None:
        """Fetch a product by GID; returns ``None`` when it does not exist."""
        query = """
            query productById($id: ID!) {
              product(id: $id) {
                id
                handle
                title
                productType
                status
                variants(first: 10) {
                  edges { node { id sku title price } }
                }
              }
            }
        """
        payload = self._graphql(query, {"id": gid}, operation="get_product")
        return (payload.get("data") or {}).get("product")

    def get_product_publish_status(self, gid: str) -> dict[str, Any] | None:
        """Read a product's publication state (2026-07 shape).

        Returns ``None`` when the product does not exist, otherwise
        ``{"id", "publishedAt", "isPublished", "publicationIds"}`` where
        ``publicationIds`` is the list of ``gid://shopify/Publication/...``
        ids the product is published to.
        """
        query = """
            query productPublishStatus($id: ID!) {
              product(id: $id) {
                id
                publishedAt
                resourcePublications(first: 25) {
                  edges { node { publication { id } } }
                }
              }
            }
        """
        payload = self._graphql(query, {"id": gid}, operation="get_product_publish_status")
        product = (payload.get("data") or {}).get("product")
        if product is None:
            return None
        publication_ids = [
            edge.get("node", {}).get("publication", {}).get("id")
            for edge in ((product.get("resourcePublications") or {}).get("edges") or [])
            if edge.get("node", {}).get("publication", {}).get("id")
        ]
        return {
            "id": product.get("id"),
            "publishedAt": product.get("publishedAt"),
            "isPublished": bool(product.get("publishedAt")),
            "publicationIds": publication_ids,
        }

    def list_products(
        self, first: int = 25, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List products with publication state (reconciliation reader).

        Returns ``(nodes, next_cursor)``; each node carries ``id``, ``handle``,
        ``title``, ``productType``, ``status``, ``publishedAt``/``published``,
        the SKUs of the first 20 variants and ``publication_ids``. Used by the
        listing-domain canonical reconciliation reader.
        """
        page = max(1, min(int(first), 25))
        query = """
            query productsPage($after: String, $first: Int!) {
              products(first: $first, after: $after) {
                edges {
                  node {
                    id
                    handle
                    title
                    productType
                    status
                    publishedAt
                    variants(first: 20) {
                      edges { node { id sku } }
                    }
                    resourcePublications(first: 25) {
                      edges { node { publication { id } } }
                    }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
        """
        payload = self._graphql(query, {"after": cursor, "first": page}, operation="list_products")
        data = payload.get("data") or {}
        edges = (data.get("products") or {}).get("edges") or []
        page_info = (data.get("products") or {}).get("pageInfo") or {}
        nodes: list[dict[str, Any]] = []
        for edge in edges:
            product = edge.get("node") or {}
            variant_skus = [
                variant_edge.get("node", {}).get("sku")
                for variant_edge in (product.get("variants") or {}).get("edges") or []
                if variant_edge.get("node", {}).get("sku")
            ]
            publication_ids = [
                pub_edge.get("node", {}).get("publication", {}).get("id")
                for pub_edge in (product.get("resourcePublications") or {}).get("edges") or []
                if pub_edge.get("node", {}).get("publication", {}).get("id")
            ]
            nodes.append(
                {
                    "id": product.get("id"),
                    "handle": product.get("handle"),
                    "title": product.get("title"),
                    "productType": product.get("productType"),
                    "status": product.get("status"),
                    "publishedAt": product.get("publishedAt"),
                    "published": bool(product.get("publishedAt")),
                    "skus": variant_skus,
                    "publication_ids": publication_ids,
                }
            )
        next_cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
        return nodes, next_cursor

    def get_fulfillment(self, gid: str) -> dict[str, Any] | None:
        """Fetch a fulfillment by GID; returns ``None`` when it does not exist."""
        query = """
            query fulfillmentById($id: ID!) {
              fulfillment(id: $id) {
                id
                status
                name
                order { id }
              }
            }
        """
        payload = self._graphql(query, {"id": gid}, operation="get_fulfillment")
        return (payload.get("data") or {}).get("fulfillment")

    def get_refund(self, gid: str) -> dict[str, Any] | None:
        """Fetch a refund by GID; returns ``None`` when it does not exist."""
        query = """
            query refundById($id: ID!) {
              refund(id: $id) {
                id
                status
                note
                totalRefundSet {
                  presentmentMoney { amount currencyCode }
                }
              }
            }
        """
        payload = self._graphql(query, {"id": gid}, operation="get_refund")
        return (payload.get("data") or {}).get("refund")


__all__ = ["ShopifyConnector"]
