"""HTTP API contract: auth, idempotency, decisions, webhooks, error envelope."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import require_roles
from app.core.errors import PermissionDeniedError
from app.models.workflow import WorkItem
from app.services.commands import dispatch_command


def _seed_procurement_work_item(db, actor_user_id) -> tuple[str, str, int]:
    """Create a procurement run and its first work item via the service layer.

    The API command path is accept-only for DBOS v2 runs (WP4); without a
    live worker no work item would exist for the decision endpoints.  Seeding
    through ``dispatch_command`` (the legacy inline engine) exercises the
    decision HTTP contract independently of the command endpoint's engine.
    """
    result = dispatch_command(
        db,
        scope=f"seed-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="procurement",
        payload={
            "sku": "SKU-API-D",
            "qty": "1",
            "supplier": "ACME",
            "unit_cost": "1.00",
        },
        actor_user_id=actor_user_id,
    )
    workflow_id = result["workflowId"]
    item = (
        db.execute(select(WorkItem).where(WorkItem.workflow_id == uuid.UUID(workflow_id)))
        .scalars()
        .one()
    )
    db.commit()
    return workflow_id, str(item.id), item.expected_version or 1


def assert_error_envelope(body: dict) -> None:
    """Every error response carries the single contract envelope."""
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "correlationId", "details"}
    assert body["error"]["message"]


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "commerce_http_requests_total" in response.text


def test_protected_routes_require_auth(client: TestClient) -> None:
    cases = [
        ("post", "/v1/catalog-revisions", {"sku": "SKU-1"}, {"Idempotency-Key": "k"}),
        (
            "post",
            "/v1/procurements",
            {"sku": "SKU-1", "supplier": "A", "unit_cost": "1"},
            {"Idempotency-Key": "k"},
        ),
        ("post", "/v1/returns", {"customer_ref": "c", "reason": "r"}, {"Idempotency-Key": "k"}),
        ("get", "/v1/workflows/019fe79a-0000-7000-8000-000000000000", None, None),
        ("get", "/v1/work-items", None, None),
    ]
    for method, path, payload, headers in cases:
        kwargs = {"headers": headers}
        if payload is not None:
            kwargs["json"] = payload
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, path
        assert_error_envelope(response.json())
        assert response.json()["error"]["code"] == "unauthenticated"


def test_require_roles_dependency(db, make_user) -> None:
    catalog_owner = make_user(["catalog_owner"])
    warehouse = make_user(["warehouse_staff"])

    assert require_roles("catalog_owner")(db=db, user_id=catalog_owner) is True
    with pytest.raises(PermissionDeniedError):
        require_roles("catalog_owner")(db=db, user_id=warehouse)
    with pytest.raises(ValueError):
        require_roles("no_such_role")


def test_create_command_without_idempotency_key_is_422(
    client: TestClient, make_user, auth_headers
) -> None:
    user_id = make_user(["catalog_owner"])
    response = client.post(
        "/v1/catalog-revisions",
        json={"sku": "SKU-1"},
        headers=auth_headers(user_id, ["catalog_owner"]),
    )
    assert response.status_code == 422
    body = response.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "validation_error"


def test_create_and_replay_command_returns_same_workflow(
    client: TestClient, make_user, auth_headers
) -> None:
    user_id = make_user(["catalog_owner"])
    headers = {**auth_headers(user_id, ["catalog_owner"]), "Idempotency-Key": "api-key-1"}
    payload = {"sku": "SKU-API-1", "title": "API Widget"}

    first = client.post("/v1/catalog-revisions", json=payload, headers=headers)
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["status"] == "accepted"
    assert first_body["statusUrl"].startswith("/v1/workflows/")
    workflow_id = first_body["workflowId"]

    replay = client.post("/v1/catalog-revisions", json=payload, headers=headers)
    assert replay.status_code == 202
    assert replay.json()["workflowId"] == workflow_id


def test_same_key_different_body_is_409(client: TestClient, make_user, auth_headers) -> None:
    user_id = make_user(["catalog_owner"])
    headers = {**auth_headers(user_id, ["catalog_owner"]), "Idempotency-Key": "api-key-2"}
    first = client.post("/v1/catalog-revisions", json={"sku": "SKU-A"}, headers=headers)
    assert first.status_code == 202
    conflict = client.post("/v1/catalog-revisions", json={"sku": "SKU-B"}, headers=headers)
    assert conflict.status_code == 409
    body = conflict.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "idempotency_key_conflict"


def test_all_command_endpoints_accept(client: TestClient, make_user, auth_headers) -> None:
    user_id = make_user(["catalog_owner", "procurement_lead", "customer_service"])
    auth = auth_headers(user_id, ["catalog_owner"])
    # Reconciliation trigger is accountant / system_admin only (计划 §四.2).
    accountant_id = make_user(["accountant"])
    accountant_auth = auth_headers(accountant_id, ["accountant"])
    cases = [
        ("/v1/catalog-revisions", {"sku": "SKU-C1"}, auth),
        ("/v1/listing-publications", {"sku": "SKU-L1", "channel": "shopify"}, auth),
        (
            "/v1/procurements",
            {"sku": "SKU-P1", "qty": "5", "supplier": "ACME", "unit_cost": "2.00"},
            auth,
        ),
        ("/v1/returns", {"customer_ref": "cust-1", "reason": "damaged"}, auth),
        ("/v1/reconciliations", {"run_type": "daily"}, accountant_auth),
    ]
    for idx, (path, payload, headers) in enumerate(cases):
        response = client.post(
            path, json=payload, headers={**headers, "Idempotency-Key": f"accept-{idx}"}
        )
        assert response.status_code == 202, path
        body = response.json()
        assert body["status"] == "accepted"
        assert uuid.UUID(body["workflowId"])


def test_decision_version_conflict_is_409(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])

    workflow_id, work_item_id, _ = _seed_procurement_work_item(db, proposer)

    stale = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": 999},
        headers={
            **auth_headers(budget_owner, ["budget_owner"]),
            "Idempotency-Key": "decision-key-1",
        },
    )
    assert stale.status_code == 409
    body = stale.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "workflow_version_conflict"


def test_decision_wrong_role_is_403(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    warehouse = make_user(["warehouse_staff"])

    _, work_item_id, expected_version = _seed_procurement_work_item(db, proposer)

    denied = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": expected_version},
        headers={
            **auth_headers(warehouse, ["warehouse_staff"]),
            "Idempotency-Key": "decision-key-2",
        },
    )
    assert denied.status_code == 403
    body = denied.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "permission_denied"


def test_decision_approve_flow(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])

    workflow_id, work_item_id, expected_version = _seed_procurement_work_item(
        db, proposer
    )

    approved = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={"decision": "approve", "reason": "ok", "expectedWorkflowVersion": expected_version},
        headers={
            **auth_headers(budget_owner, ["budget_owner"]),
            "Idempotency-Key": "decision-key-3",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["workflowId"] == workflow_id


def test_get_workflow_documented_shape(
    client: TestClient, make_user, auth_headers, db
) -> None:
    owner = make_user(["catalog_owner"])
    auth = auth_headers(owner, ["catalog_owner"])
    result = dispatch_command(
        db,
        scope=f"shape-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="catalog-revision",
        payload={"sku": "SKU-SHAPE", "title": "Shaped"},
        actor_user_id=owner,
    )
    workflow_id = result["workflowId"]
    db.commit()
    response = client.get(f"/v1/workflows/{workflow_id}", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "workflowId",
        "type",
        "status",
        "currentStep",
        "expectedWorkflowVersion",
        "input",
        "result",
        "error",
        "events",
        "effects",
        "workItems",
        "createdAt",
        "updatedAt",
    }
    assert body["workflowId"] == workflow_id
    assert body["type"] == "catalog-revision"
    assert body["status"] == "awaiting_approval"
    assert body["expectedWorkflowVersion"] == 1
    assert isinstance(body["events"], list) and body["events"]
    assert isinstance(body["workItems"], list)
    assert body["workItems"][0]["status"] == "pending"
    assert body["workItems"][0]["requiredRoles"] == ["catalog_owner"]


def test_get_missing_workflow_is_404(client: TestClient, make_user, auth_headers) -> None:
    user_id = make_user(["catalog_owner"])
    response = client.get(
        f"/v1/workflows/{uuid.uuid4()}", headers=auth_headers(user_id, ["catalog_owner"])
    )
    assert response.status_code == 404
    body = response.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "not_found"


def test_webhook_bad_hmac_is_401(client: TestClient) -> None:
    raw = json.dumps({"id": 1, "name": "#1"}).encode()
    response = client.post(
        "/v1/webhooks/shopify",
        content=raw,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": str(uuid.uuid4()),
            "X-Shopify-Hmac-Sha256": "bogus-signature",
        },
    )
    assert response.status_code == 401
    body = response.json()
    assert_error_envelope(body)
    assert body["error"]["code"] == "unauthenticated"


def test_webhook_valid_hmac_returns_received(client: TestClient) -> None:
    raw = json.dumps({"id": 1, "name": "#WEB-1", "total_price": "10.00"}).encode()
    signature = base64.b64encode(
        hmac.new(b"test-webhook-secret", raw, hashlib.sha256).digest()
    ).decode()
    response = client.post(
        "/v1/webhooks/shopify",
        content=raw,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Webhook-Id": str(uuid.uuid4()),
            "X-Shopify-Hmac-Sha256": signature,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["received"] is True
    assert body["deduplicated"] is False
