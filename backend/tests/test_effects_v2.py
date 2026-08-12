"""P7 WP5 typed effect seam: schemas, dispatch, outcome mapping, retry policy.

Covers the plan 二.4 contract:

- ``EffectExecutionRequest`` / ``EffectExecutionOutcome`` discriminated unions
  with one parameter model per ``EFFECT_OPS``.
- ``execute_effect`` fail-closed mapping: ``OutcomeUnknownError`` ->
  ``outcome_unknown`` (never blind-retried), ``RetryableEffectError`` ->
  ``failed(retryable=True)``, other ``ExternalSystemError`` ->
  ``failed(retryable=False)``, ``ConnectorError`` propagates (config error).
- No "timeout"-string inference: classification is by exception type.
- Read-back idempotency in the connectors (Odoo marker pre-check).
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings
from app.connectors.base import ConnectorError, EffectResult, OutcomeUnknownError
from app.connectors.odoo import OdooApiError, OdooConnector
from app.connectors.shopify import ShopifyConnector
from app.core.errors import ConflictError, ExternalSystemError, RetryableEffectError
from app.schemas.effects import (
    EFFECT_PARAMETER_MODELS,
    ERROR_EXPECTED_CONFLICT,
    ERROR_OUTCOME_UNKNOWN,
    ERROR_REMOTE_ERROR,
    ERROR_RETRYABLE,
    EffectExecutionRequest,
    EffectFailed,
    EffectOutcomeUnknown,
    EffectSucceeded,
    parse_effect_outcome,
    validate_effect_parameter_coverage,
)
from app.schemas.events import EFFECT_OPS
from app.services.effect_ledger import (
    _OP_METHOD,
    MAX_EFFECT_RETRY_ATTEMPTS,
    apply_outcome,
    can_retry_effect,
    execute_effect,
    mark_dispatched,
    record_effect,
    validate_effect_dispatch_coverage,
)


def _request(operation: str, **params) -> EffectExecutionRequest:
    """Build a valid request whose parameters match ``operation``."""
    model = EFFECT_PARAMETER_MODELS[operation]
    return EffectExecutionRequest(
        intent_id=uuid.uuid4(),
        operation=operation,
        parameters=model(operation=operation, **params),
        idempotency_key="key-1",
        request_hash="req-hash-1",
        correlation_id="corr-1",
    )


def _refund_request() -> EffectExecutionRequest:
    return _request(
        "shopify.refund_create",
        order_gid="gid://shopify/Order/1",
        amount="5.00",
        allow_real_money=True,
    )


class _StubConnector:
    """Adapter stub recording dispatch kwargs, returning/raising per config."""

    name = "stub"

    def __init__(self, *, returns: object = None, raises: Exception | None = None) -> None:
        self._returns = returns
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        def _call(**kwargs):
            self.calls.append((name, kwargs))
            if self._raises is not None:
                raise self._raises
            return self._returns

        return _call


def _provider(stub: _StubConnector):
    return lambda _system: stub


# ---------------------------------------------------------------------------
# Schema / discriminated union
# ---------------------------------------------------------------------------


def test_request_operation_must_match_parameters() -> None:
    with pytest.raises(PydanticValidationError, match="does not match parameters"):
        EffectExecutionRequest(
            intent_id=uuid.uuid4(),
            operation="shopify.refund_create",
            parameters=EFFECT_PARAMETER_MODELS["odoo.product_create"](
                operation="odoo.product_create", values={"name": "X"}
            ),
        )


def test_request_unknown_operation_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="unknown effect operation"):
        EffectExecutionRequest(
            intent_id=uuid.uuid4(),
            operation="shopify.no_such_op",
            parameters=EFFECT_PARAMETER_MODELS["odoo.product_create"](
                operation="odoo.product_create", values={"name": "X"}
            ),
        )


def test_outcome_union_round_trips_all_variants() -> None:
    succeeded = EffectSucceeded(
        remote_reference="gid://shopify/Refund/1",
        response_hash="h1",
        replayed=False,
    )
    parsed = parse_effect_outcome(succeeded.model_dump())
    assert isinstance(parsed, EffectSucceeded)
    assert parsed.remote_reference == "gid://shopify/Refund/1"

    failed = EffectFailed(error_code="remote_error", detail="boom", retryable=True)
    parsed = parse_effect_outcome(failed.model_dump())
    assert isinstance(parsed, EffectFailed)
    assert parsed.retryable is True

    unknown = EffectOutcomeUnknown(detail="ambiguous")
    parsed = parse_effect_outcome(unknown.model_dump())
    assert isinstance(parsed, EffectOutcomeUnknown)
    assert parsed.error_code == ERROR_OUTCOME_UNKNOWN


def test_outcome_union_rejects_bad_discriminator() -> None:
    with pytest.raises(PydanticValidationError):
        parse_effect_outcome({"outcome": "nope", "detail": "x"})


def test_every_effect_op_has_parameter_model_and_dispatch() -> None:
    validate_effect_parameter_coverage()
    validate_effect_dispatch_coverage()
    assert set(EFFECT_PARAMETER_MODELS) == set(EFFECT_OPS)
    assert set(_OP_METHOD) == set(EFFECT_OPS)


# ---------------------------------------------------------------------------
# execute_effect outcome mapping
# ---------------------------------------------------------------------------


def test_execute_succeeded_outcome() -> None:
    stub = _StubConnector(
        returns=EffectResult.succeeded("gid://shopify/Refund/1", "h1")
    )
    outcome = execute_effect(_refund_request(), connector_provider=_provider(stub))
    assert isinstance(outcome, EffectSucceeded)
    assert outcome.remote_reference == "gid://shopify/Refund/1"
    assert outcome.response_hash == "h1"
    assert outcome.replayed is False
    op, kwargs = stub.calls[0]
    assert op == "create_refund"
    assert kwargs["idempotency_key"] == "key-1"
    assert kwargs["allow_real_money"] is True


def test_execute_replayed_outcome() -> None:
    stub = _StubConnector(
        returns=EffectResult.succeeded("gid://shopify/Product/1", "h1", replayed=True)
    )
    outcome = execute_effect(
        _request("shopify.product_publish", gid="gid://shopify/Product/1"),
        connector_provider=_provider(stub),
    )
    assert isinstance(outcome, EffectSucceeded)
    assert outcome.replayed is True


def test_outcome_unknown_error_maps_to_outcome_unknown() -> None:
    stub = _StubConnector(raises=OutcomeUnknownError("remote state ambiguous"))
    outcome = execute_effect(_refund_request(), connector_provider=_provider(stub))
    assert isinstance(outcome, EffectOutcomeUnknown)
    assert outcome.error_code == ERROR_OUTCOME_UNKNOWN
    assert "ambiguous" in outcome.detail


def test_retryable_error_maps_to_retryable_failed() -> None:
    stub = _StubConnector(raises=RetryableEffectError("rate limited before processing"))
    outcome = execute_effect(
        _request("odoo.product_create", values={"name": "X"}),
        connector_provider=_provider(stub),
    )
    assert isinstance(outcome, EffectFailed)
    assert outcome.retryable is True
    assert outcome.error_code == ERROR_RETRYABLE


def test_definitive_external_error_not_retryable() -> None:
    stub = _StubConnector(raises=ExternalSystemError("definitive 4xx"))
    outcome = execute_effect(_refund_request(), connector_provider=_provider(stub))
    assert isinstance(outcome, EffectFailed)
    assert outcome.retryable is False
    assert outcome.error_code == ERROR_REMOTE_ERROR


def test_odoo_api_error_not_retryable() -> None:
    stub = _StubConnector(
        raises=OdooApiError(
            "cannot confirm", {"name": "odoo.exceptions.UserError", "message": "locked"}
        )
    )
    outcome = execute_effect(
        _request("odoo.sale_order_confirm", odoo_id=42),
        connector_provider=_provider(stub),
    )
    assert isinstance(outcome, EffectFailed)
    assert outcome.retryable is False
    assert outcome.error_code == ERROR_REMOTE_ERROR


def test_expected_conflict_maps_to_failed() -> None:
    stub = _StubConnector(returns=EffectResult.failed("already exists"))
    outcome = execute_effect(
        _request("odoo.product_create", values={"name": "X"}),
        connector_provider=_provider(stub),
    )
    assert isinstance(outcome, EffectFailed)
    assert outcome.error_code == ERROR_EXPECTED_CONFLICT
    assert outcome.retryable is False


def test_config_error_propagates_never_succeeded() -> None:
    stub = _StubConnector(raises=ConnectorError("adapter not configured"))
    with pytest.raises(ConnectorError):
        execute_effect(_refund_request(), connector_provider=_provider(stub))


def test_unknown_operation_raises_connector_error() -> None:
    request = _refund_request().model_copy(update={"operation": "shopify.no_such"})
    with pytest.raises(ConnectorError, match="no adapter dispatch"):
        execute_effect(request, connector_provider=_provider(_StubConnector()))


def test_parameter_mismatch_raises_connector_error() -> None:
    class _WrongMethod:
        def create_product(self, only_accepts: str) -> EffectResult:  # noqa: ARG001
            return EffectResult.succeeded("1", "h")

    connector = _WrongMethod()  # type: ignore[assignment]
    with pytest.raises(ConnectorError, match="does not accept"):
        execute_effect(
            _request("odoo.product_create", values={"name": "X"}),
            connector_provider=lambda _system: connector,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


def test_can_retry_only_retryable_failed_bounded_to_three() -> None:
    retryable = EffectFailed(error_code="x", detail="d", retryable=True)
    assert can_retry_effect(retryable, attempts=0)
    assert can_retry_effect(retryable, attempts=MAX_EFFECT_RETRY_ATTEMPTS - 1)
    assert not can_retry_effect(retryable, attempts=MAX_EFFECT_RETRY_ATTEMPTS)
    assert not can_retry_effect(
        EffectFailed(error_code="x", detail="d", retryable=False), attempts=0
    )
    assert not can_retry_effect(EffectOutcomeUnknown(detail="d"), attempts=0)
    assert not can_retry_effect(EffectSucceeded(remote_reference="r"), attempts=0)


# ---------------------------------------------------------------------------
# Real connector classification (exception-type, not "timeout" strings)
# ---------------------------------------------------------------------------


def _shopify_settings() -> Settings:
    return Settings(
        jwt_secret="x",
        encryption_key="x",
        shopify_shop_name="test-shop",
        shopify_access_token="shpat_abc123",
        # Empty client credentials: a real root `.env` must never trigger a
        # client-credentials token exchange while running tests from the repo
        # root (same isolation fix as test_connectors.py).
        shopify_client_id="",
        shopify_client_secret="",
        shopify_api_version="2026-07",
    )


def _shopify_connector(handler) -> ShopifyConnector:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ShopifyConnector(_shopify_settings(), client=client)


def test_timeout_and_5xx_map_to_outcome_unknown() -> None:
    def _timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    outcome = execute_effect(
        _request("shopify.product_publish", gid="gid://shopify/Product/1"),
        connector_provider=lambda _s: _shopify_connector(_timeout_handler),
    )
    assert isinstance(outcome, EffectOutcomeUnknown)

    def _5xx_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    outcome = execute_effect(
        _request("shopify.product_publish", gid="gid://shopify/Product/1"),
        connector_provider=lambda _s: _shopify_connector(_5xx_handler),
    )
    assert isinstance(outcome, EffectOutcomeUnknown)


def test_http_429_maps_to_retryable_failed() -> None:
    connector = _shopify_connector(
        lambda _request: httpx.Response(429, text="rate limited")
    )
    outcome = execute_effect(
        _request("shopify.product_publish", gid="gid://shopify/Product/1"),
        connector_provider=lambda _s: connector,
    )
    assert isinstance(outcome, EffectFailed)
    assert outcome.retryable is True


# ---------------------------------------------------------------------------
# Odoo read-before-create idempotency through the seam
# ---------------------------------------------------------------------------


def _odoo_settings() -> Settings:
    return Settings(
        jwt_secret="x",
        encryption_key="x",
        odoo_base_url="http://odoo.test",
        odoo_api_key="api-key-123",
        odoo_db="commerce",
    )


def test_odoo_create_sale_order_replayed_via_marker() -> None:
    requests: list[httpx.Request] = []
    intent_id = uuid.uuid5(uuid.NAMESPACE_DNS, "wp5-marker")
    marker = f"CO:{intent_id}"

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "search_read" in request.url.path and "sale.order" in request.url.path:
            return httpx.Response(200, json=[{"id": 77, "client_order_ref": marker}])
        raise AssertionError(f"create should not run; got {request.url.path}")

    connector = OdooConnector(
        _odoo_settings(),
        client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    request = EffectExecutionRequest(
        intent_id=intent_id,
        operation="odoo.sale_order_create",
        parameters=EFFECT_PARAMETER_MODELS["odoo.sale_order_create"](
            operation="odoo.sale_order_create", values={}
        ),
        idempotency_key="key-1",
    )
    outcome = execute_effect(request, connector_provider=lambda _s: connector)
    assert isinstance(outcome, EffectSucceeded)
    assert outcome.remote_reference == "77"
    assert outcome.replayed is True
    assert all("create" not in r.url.path for r in requests)
    # The marker must be sent on the read side too.
    search_body = json.loads(requests[0].content)
    assert marker in json.dumps(search_body)


# ---------------------------------------------------------------------------
# Ledger integration: mark_dispatched / apply_outcome / retry rules
# ---------------------------------------------------------------------------


def test_dispatch_apply_outcome_flow(db) -> None:
    intent_id = uuid.uuid4()
    record_effect(
        db,
        intent_id=intent_id,
        target_system="odoo",
        operation="product_create",
        idempotency_key="k-1",
    )
    entry = mark_dispatched(db, intent_id)
    assert entry.status.value == "dispatched"
    assert entry.attempt == 1

    stub = _StubConnector(returns=EffectResult.succeeded("42", "h1"))
    outcome = execute_effect(
        _request("odoo.product_create", values={"name": "X"}),
        connector_provider=_provider(stub),
    )
    entry = apply_outcome(db, intent_id, outcome)
    assert entry.status.value == "succeeded"
    assert entry.remote_reference == "42"


def test_apply_outcome_idempotent_replay(db) -> None:
    intent_id = uuid.uuid4()
    record_effect(db, intent_id=intent_id, target_system="shopify", operation="product_publish")
    mark_dispatched(db, intent_id)
    outcome = EffectSucceeded(remote_reference="gid://shopify/Product/1", response_hash="h")
    first = apply_outcome(db, intent_id, outcome)
    second = apply_outcome(db, intent_id, outcome)
    assert first.id == second.id


def test_apply_outcome_conflicting_outcome_rejected(db) -> None:
    intent_id = uuid.uuid4()
    record_effect(db, intent_id=intent_id, target_system="shopify", operation="product_publish")
    mark_dispatched(db, intent_id)
    apply_outcome(db, intent_id, EffectSucceeded(remote_reference="g1"))
    with pytest.raises(ConflictError, match="already succeeded"):
        apply_outcome(db, intent_id, EffectFailed(error_code="x", detail="d", retryable=False))


def test_outcome_unknown_never_redispatched(db) -> None:
    intent_id = uuid.uuid4()
    record_effect(db, intent_id=intent_id, target_system="shopify", operation="refund_create")
    mark_dispatched(db, intent_id)
    entry = apply_outcome(db, intent_id, EffectOutcomeUnknown(detail="ambiguous"))
    assert entry.status.value == "outcome_unknown"
    assert entry.compensation == "reconciliation"
    with pytest.raises(ConflictError):
        mark_dispatched(db, intent_id)
