"""P7 WP7 unit contracts: RBAC matrix, definition registry, per-operation
idempotency strategy table and the adapter contract (整改计划 §二.4 / §四.2 /
§六.1).

These tests are pure contract checks: any drift between the plan matrix, the
``EFFECT_OPS`` set, the parameter models, the dispatch table, the production
adapters and the explicit test adapter fails loudly here.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from v2_helpers import start_v2_run

from app.config import Settings
from app.connectors.odoo import OdooConnector
from app.connectors.shopify import ShopifyConnector
from app.models.identity import User
from app.schemas.effects import (
    EFFECT_PARAMETER_MODELS,
    EffectExecutionRequest,
    validate_effect_parameter_coverage,
)
from app.schemas.events import EFFECT_OPS, ROLES
from app.services.commands import COMMAND_HANDLERS
from app.services.effect_ledger import _OP_METHOD, _dispatch_kwargs
from app.services.rbac import (
    COMMAND_INITIATE_ROLES,
    DOMAIN_READ_ROLES,
    OPS_RETRY_ROLES,
    RECONCILIATION_RESOLVE_ROLES,
    ensure_roles,
    has_role,
)

# ---------------------------------------------------------------------------
# Strict domain RBAC matrix (计划 §四.2)
# ---------------------------------------------------------------------------


def test_command_initiate_matrix_matches_plan() -> None:
    """The five command scopes map exactly to the plan's initiating roles."""
    assert COMMAND_INITIATE_ROLES == {
        "catalog-revision": ("catalog_owner",),
        "listing-publication": ("catalog_owner",),
        "procurement": ("procurement_lead",),
        "return": ("customer_service",),
        "reconciliation": ("accountant", "system_admin"),
    }
    # Every command scope has a registered handler (the API cannot accept a
    # command the domain does not implement).  The webhook-driven workflow
    # types are domain entries without a user-initiated command scope.
    assert set(COMMAND_HANDLERS) == set(COMMAND_INITIATE_ROLES) | {
        "order-to-cash",
        "return-to-refund",
    }


def test_read_matrix_matches_plan() -> None:
    assert DOMAIN_READ_ROLES["sales_orders"] == (
        "customer_service",
        "warehouse_staff",
        "finance_approver",
        "accountant",
        "system_admin",
    )
    assert DOMAIN_READ_ROLES["return_cases"] == DOMAIN_READ_ROLES["sales_orders"]
    assert DOMAIN_READ_ROLES["procurements"] == (
        "procurement_lead",
        "budget_owner",
        "warehouse_staff",
        "accountant",
        "system_admin",
    )
    assert DOMAIN_READ_ROLES["reconciliation"] == ("accountant", "compliance", "system_admin")
    assert DOMAIN_READ_ROLES["ops"] == ("system_admin",)
    # workflows / work items: any valid business role.
    assert set(DOMAIN_READ_ROLES["workflows"]) == set(ROLES)
    assert set(DOMAIN_READ_ROLES["work_items"]) == set(ROLES)


def test_all_matrix_roles_are_valid() -> None:
    for roles in (
        *COMMAND_INITIATE_ROLES.values(),
        *DOMAIN_READ_ROLES.values(),
        RECONCILIATION_RESOLVE_ROLES,
        OPS_RETRY_ROLES,
    ):
        for role in roles:
            assert role in ROLES, f"unknown role {role!r} in matrix"
    assert RECONCILIATION_RESOLVE_ROLES == ("accountant", "system_admin")
    assert OPS_RETRY_ROLES == ("system_admin",)


def test_has_role_and_ensure_roles(db, make_user) -> None:
    owner = make_user(["catalog_owner"])
    assert has_role(db, owner, "catalog_owner") is True
    assert has_role(db, owner, "system_admin") is False
    assert has_role(db, owner, "no_such_role") is False
    ensure_roles(db, owner, ["catalog_owner", "system_admin"])  # one suffices

    from app.core.errors import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        ensure_roles(db, owner, ["system_admin"])
    ensure_roles(db, owner, [])  # empty roles always pass


def test_inactive_user_cannot_submit_decision(
    client: TestClient, make_user, auth_headers, db
) -> None:
    """计划 §四.2: get_current_user validates ``is_active`` — an inactive user
    is rejected (401) even when they hold the required role."""
    proposer = make_user(["procurement_lead"])
    approver = make_user(["budget_owner"])
    run, items = start_v2_run(
        db,
        "procurement",
        {"sku": "SKU-I", "qty": "1", "supplier": "ACME", "unit_cost": "1.00"},
        actor_user_id=proposer,
    )
    item = items[0]
    db.get(User, approver).is_active = False
    db.commit()

    response = client.post(
        f"/v1/work-items/{item.id}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": item.expected_version},
        headers={
            **auth_headers(approver, ["budget_owner"]),
            "Idempotency-Key": "inactive-decision",
        },
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Workflow definition registry consistency (计划 §二.1)
# ---------------------------------------------------------------------------


def test_definition_registry_covers_every_command_and_rejects_unknown(
) -> None:
    """Every command handler has a DBOS v2 definition and unknown pairs fail.

    ``app.workflows.definitions`` imports ``dbos`` at module level, so the
    check runs in a subprocess to keep this file free of the runtime (same
    pattern as test_workflow_v2).
    """
    script = """
from app.services.commands import COMMAND_HANDLERS
from app.workflows.definitions import WORKFLOW_DEFINITIONS, resolve_definition

missing = set(COMMAND_HANDLERS) - {
    t for (t, v) in WORKFLOW_DEFINITIONS if v == 2
}
if missing:
    raise SystemExit(f"commands without a v2 definition: {sorted(missing)}")
extra = {
    t for (t, v) in WORKFLOW_DEFINITIONS if v != 2
}
if extra:
    raise SystemExit(f"non-v2 definitions registered: {sorted(extra)}")
for workflow_type in COMMAND_HANDLERS:
    fn = resolve_definition(workflow_type, 2)
    assert callable(fn), workflow_type
try:
    resolve_definition("procurement", 1)
    raise SystemExit("expected ValueError for (procurement, 1)")
except ValueError:
    pass
print("registry-ok")
"""
    backend_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "registry-ok" in proc.stdout


# ---------------------------------------------------------------------------
# Per-operation idempotency / read-back strategy table (计划 §二.4)
# ---------------------------------------------------------------------------

IDEMPOTENCY_STRATEGIES: dict[str, str] = {
    "shopify.refund_create": "native_idempotent_directive",
    "shopify.product_update": "read_back",
    "shopify.product_publish": "read_back",
    "shopify.fulfillment_create": "precheck_then_read_back",
    "odoo.product_create": "query_before_create",
    "odoo.product_update": "write_by_id",
    "odoo.sale_order_create": "marker_query_before_create",
    "odoo.sale_order_confirm": "state_precheck",
    "odoo.stock_move_create": "marker_query_before_create",
    "odoo.picking_create": "marker_query_before_create",
    "odoo.picking_validate": "state_precheck",
    "odoo.receive_transfer": "state_precheck",
    "odoo.invoice_create": "marker_query_before_create",
    "odoo.invoice_validate": "state_precheck",
    "odoo.credit_note_create": "marker_query_before_create",
    "odoo.credit_note_validate": "state_precheck",
    "odoo.po_create": "marker_query_before_create",
    "odoo.po_confirm": "state_precheck",
    "odoo.bill_create": "marker_query_before_create",
}

_KNOWN_STRATEGIES = frozenset(
    {
        "native_idempotent_directive",
        "read_back",
        "precheck_then_read_back",
        "query_before_create",
        "marker_query_before_create",
        "state_precheck",
        "write_by_id",
    }
)


def test_idempotency_strategy_table_covers_every_effect_op() -> None:
    """Every ``EFFECT_OPS`` entry has a documented strategy and the table does
    not drift from the parameter-model / dispatch sets."""
    assert set(IDEMPOTENCY_STRATEGIES) == set(EFFECT_OPS)
    assert all(strategy in _KNOWN_STRATEGIES for strategy in IDEMPOTENCY_STRATEGIES.values())
    validate_effect_parameter_coverage()
    assert set(_OP_METHOD) == set(EFFECT_OPS)


def test_shopify_refund_strategy_is_native_idempotent() -> None:
    """退款用原生 @idempotent 指令：同 key 安全重试（计划表 + 实现）。"""
    assert IDEMPOTENCY_STRATEGIES["shopify.refund_create"] == "native_idempotent_directive"
    src = inspect.getsource(ShopifyConnector.create_refund)
    assert "@idempotent(key: $key)" in src


def test_shopify_publish_and_update_read_back() -> None:
    """publish/update 按 GID/publication 读回：目标状态已存在视为成功。"""
    publish_src = inspect.getsource(ShopifyConnector.publish_product)
    assert "get_product_publish_status" in publish_src
    update_src = inspect.getsource(ShopifyConnector.update_product)
    assert "get_product" in update_src
    assert "_payload_matches_product" in update_src


def test_odoo_create_ops_stamp_marker_and_query_first() -> None:
    """Odoo create 类操作把 CO:<intent_id> 写入引用字段并先查后建。"""
    for method_name, _marker in (
        ("create_sale_order", "client_order_ref"),
        ("create_po", "partner_ref"),
        ("create_bill", "ref"),
        ("create_invoice", "ref"),
        ("create_credit_note", "ref"),
        ("create_stock_move", "origin"),
        ("create_picking", "origin"),
    ):
        src = inspect.getsource(getattr(OdooConnector, method_name))
        assert "CO:<intent_id>" in src, method_name
        assert "_search_one" in src or "search" in src, method_name


def test_odoo_state_transition_ops_precheck() -> None:
    """confirm/validate/post/receive 类操作调用前读状态，已达目标视为成功。"""
    for method_name in (
        "confirm_sale_order",
        "validate_picking",
        "receive_transfer",
        "validate_invoice",
        "validate_credit_note",
        "confirm_po",
    ):
        src = inspect.getsource(getattr(OdooConnector, method_name))
        assert "_read_state" in src, method_name


# ---------------------------------------------------------------------------
# Adapter contract: every EFFECT_OPS has a production adapter method and an
# explicit test adapter; dispatch kwargs stay signature-compatible (计划 §二.4).
# ---------------------------------------------------------------------------


class InMemoryEffectAdapter:
    """Explicit test adapter implementing every ``EFFECT_OPS`` method.

    The adapter contract test asserts this class's public method set equals
    the production dispatch set: adding an effect operation without a test
    double fails the suite (计划 §六.1 "所有 EFFECT_OPS 都有生产 adapter 和
    测试 adapter；集合不一致时测试失败").
    """

    name = "inmemory"

    def __init__(
        self,
        *,
        result=None,
        raises: Exception | None = None,
    ) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def _handle(self, method: str, kwargs: dict):
        self.calls.append((method, dict(kwargs)))
        if self._raises is not None:
            raise self._raises
        if self._result is not None:
            return self._result
        from app.connectors.base import EffectResult

        return EffectResult.succeeded(f"remote:{method}", f"hash:{method}")

    # Shopify
    def create_refund(self, **kwargs):
        return self._handle("create_refund", kwargs)

    def update_product(self, **kwargs):
        return self._handle("update_product", kwargs)

    def publish_product(self, **kwargs):
        return self._handle("publish_product", kwargs)

    def create_fulfillment(self, **kwargs):
        return self._handle("create_fulfillment", kwargs)

    # Odoo
    def create_product(self, **kwargs):
        return self._handle("create_product", kwargs)

    def create_sale_order(self, **kwargs):
        return self._handle("create_sale_order", kwargs)

    def confirm_sale_order(self, **kwargs):
        return self._handle("confirm_sale_order", kwargs)

    def create_stock_move(self, **kwargs):
        return self._handle("create_stock_move", kwargs)

    def create_picking(self, **kwargs):
        return self._handle("create_picking", kwargs)

    def validate_picking(self, **kwargs):
        return self._handle("validate_picking", kwargs)

    def receive_transfer(self, **kwargs):
        return self._handle("receive_transfer", kwargs)

    def create_invoice(self, **kwargs):
        return self._handle("create_invoice", kwargs)

    def validate_invoice(self, **kwargs):
        return self._handle("validate_invoice", kwargs)

    def create_credit_note(self, **kwargs):
        return self._handle("create_credit_note", kwargs)

    def validate_credit_note(self, **kwargs):
        return self._handle("validate_credit_note", kwargs)

    def create_po(self, **kwargs):
        return self._handle("create_po", kwargs)

    def confirm_po(self, **kwargs):
        return self._handle("confirm_po", kwargs)

    def create_bill(self, **kwargs):
        return self._handle("create_bill", kwargs)


def _public_methods(cls) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name, None))
    }


def test_adapter_contract_production_and_test_adapters_cover_all_ops() -> None:
    """Every EFFECT_OPS op has a production adapter method and a test adapter
    method; any set mismatch fails (计划 §六.1)."""
    assert set(_OP_METHOD) == set(EFFECT_OPS)
    for operation, method in _OP_METHOD.items():
        system = operation.split(".", 1)[0]
        connector_cls = {
            "shopify": ShopifyConnector,
            "odoo": OdooConnector,
        }[system]
        assert hasattr(connector_cls, method), (
            f"{operation} has no production adapter method {method!r}"
        )
    assert _public_methods(InMemoryEffectAdapter) == set(_OP_METHOD.values()), (
        "test adapter method set drifted from the production dispatch set"
    )


@pytest.mark.parametrize("operation", sorted(EFFECT_OPS))
def test_adapter_dispatch_kwargs_match_signature(operation: str) -> None:
    """The typed parameter model's kwargs fit the adapter method signature."""
    model = EFFECT_PARAMETER_MODELS[operation]
    method = getattr(
        {
            "shopify": ShopifyConnector,
            "odoo": OdooConnector,
        }[operation.split(".", 1)[0]],
        _OP_METHOD[operation],
    )
    request = EffectExecutionRequest(
        intent_id=uuid.uuid4(),
        operation=operation,
        parameters=model(operation=operation, **(_sample_params(operation))),
        idempotency_key="key-1",
        request_hash="h",
        correlation_id="c",
    )
    # Raises ConnectorError on a signature mismatch — that would fail the test.
    kwargs = _dispatch_kwargs(method, request)
    assert kwargs  # at least the parameter fields are dispatched


def _sample_params(operation: str) -> dict:
    """Minimal valid parameter values per operation family."""
    if operation.startswith("shopify.refund"):
        return {"order_gid": "gid://shopify/Order/1", "amount": "5.00"}
    if operation == "shopify.product_update":
        return {"gid": "gid://shopify/Product/1", "payload": {"title": "T"}}
    if operation == "shopify.product_publish":
        return {"gid": "gid://shopify/Product/1"}
    if operation == "shopify.fulfillment_create":
        return {"order_gid": "gid://shopify/Order/1"}
    if operation == "odoo.product_update":
        return {"odoo_id": 1, "values": {"name": "X"}}
    if operation.endswith("_confirm") or operation.endswith("_validate"):
        return {"odoo_id": 1}
    if operation == "odoo.receive_transfer":
        return {"odoo_id": 1}
    return {"values": {"name": "X"}}


# ---------------------------------------------------------------------------
# Readiness: adapters fail closed when not configured (计划 §五.1 / §二.4)
# ---------------------------------------------------------------------------


def test_readyz_adapters_check_fails_closed(monkeypatch) -> None:
    import app.main
    from app.main import _check_adapters

    def _settings(**overrides) -> Settings:
        base = {
            "jwt_secret": "x",
            "encryption_key": "x",
            "shopify_shop_name": "shop",
            "shopify_access_token": "tok",
            "odoo_base_url": "http://odoo",
            "odoo_api_key": "key",
        }
        base.update(overrides)
        return Settings(**base)

    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: _settings(),
    )
    assert _check_adapters()["status"] == "ok"
    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: _settings(shopify_shop_name="", shopify_access_token=""),
    )
    assert _check_adapters()["status"] == "fail"
    assert "shopify" in _check_adapters()["message"]
    monkeypatch.setattr(
        app.main,
        "get_settings",
        lambda: _settings(odoo_base_url="", odoo_api_key=""),
    )
    assert _check_adapters()["status"] == "fail"
    assert "odoo" in _check_adapters()["message"]


__all__ = ["IDEMPOTENCY_STRATEGIES", "InMemoryEffectAdapter"]
