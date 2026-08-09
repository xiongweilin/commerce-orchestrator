"""Connector contracts: Shopify GraphQL, Odoo JSON-2 and Metabase probe."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.connectors.base import ConnectorError, EffectResult, OutcomeUnknownError
from app.connectors.metabase import MetabaseHealth
from app.connectors.odoo import OdooApiError, OdooConnector
from app.connectors.shopify import ShopifyConnector
from app.core.errors import ExternalSystemError


def _shopify_settings(**overrides) -> Settings:
    base = {
        "jwt_secret": "x",
        "encryption_key": "x",
        "shopify_shop_name": "test-shop",
        "shopify_access_token": "shpat_abc123",
        "shopify_api_version": "2026-07",
    }
    base.update(overrides)
    return Settings(**base)


def _odoo_settings(**overrides) -> Settings:
    base = {
        "jwt_secret": "x",
        "encryption_key": "x",
        "odoo_base_url": "http://odoo.test",
        "odoo_api_key": "api-key-123",
        "odoo_db": "commerce",
    }
    base.update(overrides)
    return Settings(**base)


def _shopify_connector(
    handler, **settings_overrides
) -> tuple[ShopifyConnector, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def _recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(_recording_handler))
    connector = ShopifyConnector(_shopify_settings(**settings_overrides), client=client)
    return connector, requests


# ---------------------------------------------------------------------------
# Shopify
# ---------------------------------------------------------------------------


def test_shopify_create_product_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/admin/api/2026-07/graphql.json" in str(request.url)
        assert request.headers["X-Shopify-Access-Token"] == "shpat_abc123"
        return httpx.Response(
            200,
            json={
                "data": {
                    "productCreate": {
                        "product": {"id": "gid://shopify/Product/101"},
                        "userErrors": [],
                    }
                }
            },
        )

    connector, requests = _shopify_connector(handler)
    result = connector.create_product({"title": "Widget"})
    assert result.ok is True
    assert result.status == "succeeded"
    assert result.remote_reference == "gid://shopify/Product/101"
    assert result.response_hash
    assert len(requests) == 1
    assert "productCreate" in requests[0].content.decode()


def test_shopify_user_errors_yield_failed_effect_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "productCreate": {
                        "product": None,
                        "userErrors": [{"field": "title", "message": "has already been taken"}],
                    }
                }
            },
        )

    connector, _ = _shopify_connector(handler)
    result = connector.create_product({"title": "Widget"})
    assert result.ok is False
    assert result.status == "failed"
    assert "conflict" in (result.error or "")


def test_shopify_top_level_graphql_errors_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "something exploded"}]})

    connector, _ = _shopify_connector(handler)
    with pytest.raises(ExternalSystemError, match="top-level"):
        connector.create_product({"title": "Widget"})


def test_shopify_publish_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "publishablePublish": {
                        "publishable": {"id": "gid://shopify/Product/101"},
                        "userErrors": [],
                    }
                }
            },
        )

    connector, _ = _shopify_connector(handler)
    result = connector.publish_product("gid://shopify/Product/101")
    assert result.ok is True
    assert result.remote_reference == "gid://shopify/Product/101"


def test_shopify_refund_requires_real_money_and_idempotency_key() -> None:
    connector, _ = _shopify_connector(lambda request: httpx.Response(500))
    with pytest.raises(ConnectorError, match="allow_real_money"):
        connector.create_refund("gid://shopify/Order/1", "5.00", idempotency_key="k-1")
    with pytest.raises(ConnectorError, match="idempotency_key"):
        connector.create_refund(
            "gid://shopify/Order/1", "5.00", allow_real_money=True, idempotency_key=None
        )


def test_shopify_refund_success_with_guards() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "refundCreate": {
                        "refund": {"id": "gid://shopify/Refund/7"},
                        "userErrors": [],
                    }
                }
            },
        )

    connector, _ = _shopify_connector(handler)
    result = connector.create_refund(
        "gid://shopify/Order/1",
        "5.00",
        allow_real_money=True,
        idempotency_key="refund-k",
    )
    assert result.ok is True
    assert result.remote_reference == "gid://shopify/Refund/7"


def test_shopify_timeout_raises_outcome_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    connector, _ = _shopify_connector(handler)
    with pytest.raises(OutcomeUnknownError) as excinfo:
        connector.create_product({"title": "Widget"})
    assert excinfo.value.status == "outcome_unknown"


def test_shopify_5xx_raises_outcome_unknown() -> None:
    connector, _ = _shopify_connector(lambda request: httpx.Response(503, text="unavailable"))
    with pytest.raises(OutcomeUnknownError):
        connector.create_product({"title": "Widget"})


def test_shopify_unconfigured_raises_connector_error() -> None:
    connector = ShopifyConnector(
        _shopify_settings(shopify_shop_name="", shopify_access_token=""),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    with pytest.raises(ConnectorError, match="not configured"):
        connector.create_product({"title": "Widget"})


# ---------------------------------------------------------------------------
# Odoo
# ---------------------------------------------------------------------------


def _odoo_connector(handler) -> tuple[OdooConnector, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def _recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(_recording_handler))
    return OdooConnector(_odoo_settings(), client=client), requests


def test_odoo_create_product_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://odoo.test/json/2/product.template/create"
        assert request.headers["Authorization"] == "bearer api-key-123"
        assert request.headers["X-Odoo-Database"] == "commerce"
        return httpx.Response(200, json=42)

    connector, requests = _odoo_connector(handler)
    result = connector.create_product({"name": "Widget", "default_code": "SKU-1"})
    assert isinstance(result, EffectResult)
    assert result.ok is True
    assert result.remote_reference == "42"
    assert len(requests) == 1
    assert b'"values"' in requests[0].content


def test_odoo_error_body_raises_external_system_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "name": "odoo.exceptions.UserError",
                "message": "Cannot confirm order because it is locked",
                "arguments": [],
                "context": {},
                "debug": "",
            },
        )

    connector, _ = _odoo_connector(handler)
    with pytest.raises(OdooApiError) as excinfo:
        connector.confirm_sale_order(42)
    assert isinstance(excinfo.value, ExternalSystemError)
    assert excinfo.value.error_body["name"] == "odoo.exceptions.UserError"


def test_odoo_expected_conflict_returns_failed_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"name": "odoo.exceptions.ValidationError", "message": "already exists"},
        )

    connector, _ = _odoo_connector(handler)
    result = connector.create_product({"name": "Widget"})
    assert result.ok is False
    assert result.status == "failed"
    assert "conflict" in (result.error or "")


def test_odoo_probe_failure_mentions_adr0008_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    connector, _ = _odoo_connector(handler)
    with pytest.raises(ExternalSystemError, match="ADR-0008"):
        connector.probe()


def test_odoo_probe_success() -> None:
    connector, _ = _odoo_connector(lambda request: httpx.Response(200, json=[{"id": 1}]))
    result = connector.probe()
    assert result["ok"] is True
    assert result["json2_available"] is True


def test_odoo_unconfigured_raises_connector_error() -> None:
    connector = OdooConnector(
        _odoo_settings(odoo_base_url="", odoo_api_key=""),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    with pytest.raises(ConnectorError, match="not configured"):
        connector.probe()


# ---------------------------------------------------------------------------
# Metabase
# ---------------------------------------------------------------------------


def test_metabase_probe_graceful_when_not_configured() -> None:
    settings = Settings(jwt_secret="x", encryption_key="x", environment="prod")
    health = MetabaseHealth(settings=settings)
    result = health.probe()
    assert result["ok"] is False
    assert result["configured"] is False
    assert "not configured" in result["detail"]


def test_metabase_probe_unreachable_does_not_raise() -> None:
    settings = Settings(jwt_secret="x", encryption_key="x", environment="dev")

    def _raise(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(_raise))
    health = MetabaseHealth(settings=settings, client=client)
    result = health.probe()
    assert result["ok"] is False
    assert result["configured"] is True


def test_metabase_probe_healthy() -> None:
    settings = Settings(jwt_secret="x", encryption_key="x", environment="dev")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "healthy", "version": "v50"})
        )
    )
    health = MetabaseHealth(settings=settings, client=client)
    result = health.probe()
    assert result["ok"] is True
    assert result["version"] == "v50"
