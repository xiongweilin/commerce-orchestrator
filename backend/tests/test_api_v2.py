"""WP6 API v2: /v1/me, /livez, /readyz, ops endpoints, strict RBAC and
Idempotency-Key semantics (整改计划 §四.1 / §四.2).

The reconciliation trigger follows the plan matrix (accountant /
system_admin); ``tests/test_api.py`` covers the positive path with an
accountant user.  The write command endpoints are accept-only for DBOS v2
runs (WP4), so tests that need a concrete work item seed the run through the
service layer (``dispatch_command``, legacy inline engine) instead of the
command endpoint — this keeps the decision HTTP contract testable without a
live worker and independent of the command path's engine.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.identity import User
from app.models.messaging import IdempotencyRecord, InboxEvent, InboxStatus
from app.models.reconciliation import ReconciliationDiff
from app.models.workflow import WorkItem, WorkItemDecision
from app.services.commands import canonical_hash, dispatch_command
from app.services.reconciliation import run_reconciliation
from app.services.workflows import IDEMPOTENCY_SCOPE_DECISION


def _assert_error_envelope(body: dict, code: str) -> None:
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


def test_livez_ok(client: TestClient) -> None:
    response = client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_livez_alias(client: TestClient) -> None:
    assert client.get("/healthz").json() == client.get("/livez").json()


def test_readyz_shape_not_ready_without_migrations(client: TestClient) -> None:
    """In the sqlite test schema (create_all, no alembic/worker) the probe
    must fail loudly with a per-check breakdown instead of crashing."""
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    checks = body["checks"]
    assert set(checks) == {"database", "alembic", "adapters", "worker"}
    assert checks["database"]["status"] == "ok"  # sqlite answers SELECT 1
    assert checks["alembic"]["status"] == "fail"  # no alembic_version table
    assert checks["adapters"]["status"] == "ok"  # conftest sets adapter env
    assert checks["worker"]["status"] == "fail"  # no worker heartbeat


# ---------------------------------------------------------------------------
# GET /v1/me
# ---------------------------------------------------------------------------


def test_me_requires_auth(client: TestClient) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401
    _assert_error_envelope(response.json(), "unauthenticated")


def test_me_returns_db_roles_not_jwt_claims(
    client: TestClient, make_user, auth_headers
) -> None:
    user_id = make_user(["catalog_owner"])
    # JWT role claims (system_admin) are informational: the DB assignment
    # (catalog_owner) is authoritative.
    response = client.get("/v1/me", headers=auth_headers(user_id, ["system_admin"]))
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user_id)
    assert body["roles"] == ["catalog_owner"]
    assert body["isActive"] is True
    assert body["email"]
    assert body["jwtExpiresAt"] and body["jwtExpiresAt"].endswith("Z")


def test_me_rejects_inactive_user(client: TestClient, make_user, auth_headers, db) -> None:
    user_id = make_user(["catalog_owner"])
    user = db.get(User, user_id)
    user.is_active = False
    db.commit()
    headers = auth_headers(user_id, ["catalog_owner"])
    assert client.get("/v1/me", headers=headers).status_code == 401
    assert client.get("/v1/workflows", headers=headers).status_code == 401


def test_me_rejects_unknown_user(client: TestClient, auth_headers) -> None:
    response = client.get("/v1/me", headers=auth_headers(uuid.uuid4(), []))
    assert response.status_code == 401
    _assert_error_envelope(response.json(), "unauthenticated")


# ---------------------------------------------------------------------------
# Strict command RBAC (整改计划 §四.2 command initiation matrix)
# ---------------------------------------------------------------------------


def test_command_initiation_rbac_matrix(client: TestClient, make_user, auth_headers) -> None:
    cases = [
        ("/v1/catalog-revisions", {"sku": "SKU-R1"}, "catalog_owner", "customer_service"),
        (
            "/v1/listing-publications",
            {"sku": "SKU-R2", "channel": "shopify"},
            "catalog_owner",
            "warehouse_staff",
        ),
        (
            "/v1/procurements",
            {"sku": "SKU-R3", "qty": "1", "supplier": "ACME", "unit_cost": "1.00"},
            "procurement_lead",
            "catalog_owner",
        ),
        (
            "/v1/returns",
            {"customer_ref": "c-1", "reason": "damaged"},
            "customer_service",
            "budget_owner",
        ),
    ]
    for idx, (path, payload, allowed_role, denied_role) in enumerate(cases):
        allowed_user = make_user([allowed_role])
        denied_user = make_user([denied_role])
        ok = client.post(
            path,
            json=payload,
            headers={**auth_headers(allowed_user, [allowed_role]), "Idempotency-Key": f"ok-{idx}"},
        )
        assert ok.status_code == 202, f"{path} allowed role failed: {ok.text}"
        denied = client.post(
            path,
            json=payload,
            headers={**auth_headers(denied_user, [denied_role]), "Idempotency-Key": f"no-{idx}"},
        )
        assert denied.status_code == 403, f"{path} denied role failed: {denied.text}"
        _assert_error_envelope(denied.json(), "permission_denied")


def test_reconciliation_trigger_positive_accountant(
    client: TestClient, make_user, auth_headers
) -> None:
    user_id = make_user(["accountant"])
    response = client.post(
        "/v1/reconciliations",
        json={"run_type": "daily", "domains": ["effect"]},
        headers={**auth_headers(user_id, ["accountant"]), "Idempotency-Key": "rec-trigger-1"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_read_matrix(client: TestClient, make_user, auth_headers) -> None:
    def status(path: str, user_id: uuid.UUID, roles: list[str]) -> int:
        return client.get(path, headers=auth_headers(user_id, roles)).status_code

    # workflows: any business role reads, no-role user is denied.
    any_role = make_user(["customer_service"])
    no_role = make_user([])
    assert status("/v1/workflows", any_role, ["customer_service"]) == 200
    assert status("/v1/workflows", no_role, []) == 403
    # sales-orders / return-cases: customer_service in, procurement_lead out.
    cs = make_user(["customer_service"])
    pl = make_user(["procurement_lead"])
    assert status("/v1/sales-orders", cs, ["customer_service"]) == 200
    assert status("/v1/sales-orders", pl, ["procurement_lead"]) == 403
    assert status("/v1/return-cases", cs, ["customer_service"]) == 200
    assert status("/v1/return-cases", pl, ["procurement_lead"]) == 403
    # procurements: procurement_lead in, customer_service out.
    assert status("/v1/procurements", pl, ["procurement_lead"]) == 200
    assert status("/v1/procurements", cs, ["customer_service"]) == 403
    # reconciliation reads: accountant / compliance / system_admin in.
    warehouse = make_user(["warehouse_staff"])
    for role_name in ("accountant", "compliance", "system_admin"):
        role_user = make_user([role_name])
        assert status("/v1/reconciliations", role_user, [role_name]) == 200
    assert status("/v1/reconciliations", warehouse, ["warehouse_staff"]) == 403


# ---------------------------------------------------------------------------
# Decision permissions: no implicit system_admin approval, compliance veto
# ---------------------------------------------------------------------------


def _seed_procurement_work_item(db, proposer) -> tuple[str, str, int]:
    """Seed a procurement run + first work item via the service layer."""
    result = dispatch_command(
        db,
        scope=f"seed-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="procurement",
        payload={"sku": "SKU-D", "qty": "1", "supplier": "ACME", "unit_cost": "1.00"},
        actor_user_id=proposer,
    )
    item = (
        db.execute(select(WorkItem).where(WorkItem.workflow_id == uuid.UUID(result["workflowId"])))
        .scalars()
        .one()
    )
    db.commit()
    return result["workflowId"], str(item.id), item.expected_version or 1


def _seed_catalog_revision(db, owner, sku: str = "SKU-C1") -> str:
    """Seed a catalog-revision run via the service layer; return workflow id."""
    result = dispatch_command(
        db,
        scope=f"catalog-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="catalog-revision",
        payload={"sku": sku},
        actor_user_id=owner,
    )
    db.commit()
    return result["workflowId"]


def test_system_admin_does_not_implicitly_approve(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    _, work_item_id, expected_version = _seed_procurement_work_item(db, proposer)
    admin = make_user(["system_admin"])
    response = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": int(expected_version)},
        headers={**auth_headers(admin, ["system_admin"]), "Idempotency-Key": "admin-approve-1"},
    )
    assert response.status_code == 403
    _assert_error_envelope(response.json(), "permission_denied")


def test_compliance_can_reject_but_not_approve(
    client: TestClient, make_user, auth_headers, db
) -> None:
    compliance = make_user(["compliance"])
    headers = auth_headers(compliance, ["compliance"])

    # approve is denied (compliance only vetoes).
    owner = make_user(["catalog_owner"])
    auth = auth_headers(owner, ["catalog_owner"])
    workflow_id = _seed_catalog_revision(db, owner, "SKU-C1")
    detail = client.get(f"/v1/workflows/{workflow_id}", headers=auth).json()
    item = detail["workItems"][0]
    denied = client.post(
        f"/v1/work-items/{item['workItemId']}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": item["expectedWorkflowVersion"]},
        headers={**headers, "Idempotency-Key": "compliance-approve-1"},
    )
    assert denied.status_code == 403

    # reject (veto) is allowed and cancels the run.
    workflow_id2 = _seed_catalog_revision(db, owner, "SKU-C2")
    detail2 = client.get(f"/v1/workflows/{workflow_id2}", headers=auth).json()
    item2 = detail2["workItems"][0]
    veto = client.post(
        f"/v1/work-items/{item2['workItemId']}/decisions",
        json={
            "decision": "reject",
            "reason": "policy",
            "expectedWorkflowVersion": item2["expectedWorkflowVersion"],
        },
        headers={**headers, "Idempotency-Key": "compliance-veto-1"},
    )
    assert veto.status_code == 200
    assert veto.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# Decision Idempotency-Key semantics
# ---------------------------------------------------------------------------


def test_decision_idempotent_replay(client: TestClient, make_user, auth_headers, db) -> None:
    proposer = make_user(["procurement_lead"])
    _, work_item_id, expected_version = _seed_procurement_work_item(db, proposer)
    approver = make_user(["budget_owner"])
    headers = {
        **auth_headers(approver, ["budget_owner"]),
        "Idempotency-Key": "decision-replay-1",
    }
    body = {"decision": "approve", "reason": "ok", "expectedWorkflowVersion": int(expected_version)}
    first = client.post(f"/v1/work-items/{work_item_id}/decisions", json=body, headers=headers)
    assert first.status_code == 200
    replay = client.post(f"/v1/work-items/{work_item_id}/decisions", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["workItemId"] == first.json()["workItemId"]
    assert replay.json()["status"] == first.json()["status"]
    assert db.execute(select(WorkItemDecision)).scalars().all() and len(
        db.execute(select(WorkItemDecision)).scalars().all()
    ) == 1


def test_decision_same_key_different_body_409(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    _, work_item_id, expected_version = _seed_procurement_work_item(db, proposer)
    approver = make_user(["budget_owner"])
    headers = {
        **auth_headers(approver, ["budget_owner"]),
        "Idempotency-Key": "decision-conflict-1",
    }
    first = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={
            "decision": "approve",
            "reason": "a",
            "expectedWorkflowVersion": int(expected_version),
        },
        headers=headers,
    )
    assert first.status_code == 200
    conflict = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json={
            "decision": "approve",
            "reason": "b",
            "expectedWorkflowVersion": int(expected_version),
        },
        headers=headers,
    )
    assert conflict.status_code == 409
    _assert_error_envelope(conflict.json(), "idempotency_key_conflict")


def test_decision_in_progress_is_409_with_retry_after(
    client: TestClient, make_user, auth_headers, db
) -> None:
    proposer = make_user(["procurement_lead"])
    _, work_item_id, expected_version = _seed_procurement_work_item(db, proposer)
    approver = make_user(["budget_owner"])
    body = {"decision": "approve", "reason": "ok", "expectedWorkflowVersion": int(expected_version)}
    request_hash = canonical_hash(
        {
            "work_item_id": work_item_id,
            "decision": "approve",
            "expected_version": int(expected_version),
            "reason": "ok",
        }
    )
    db.add(
        IdempotencyRecord(
            scope=IDEMPOTENCY_SCOPE_DECISION,
            key="decision-in-progress-1",
            request_hash=request_hash,
            status="processing",
        )
    )
    db.commit()
    response = client.post(
        f"/v1/work-items/{work_item_id}/decisions",
        json=body,
        headers={
            **auth_headers(approver, ["budget_owner"]),
            "Idempotency-Key": "decision-in-progress-1",
        },
    )
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "1"
    _assert_error_envelope(response.json(), "idempotency_in_progress")


# ---------------------------------------------------------------------------
# Ops endpoints (system_admin only)
# ---------------------------------------------------------------------------


def test_ops_requires_system_admin(client: TestClient, make_user, auth_headers) -> None:
    accountant = make_user(["accountant"])
    headers = auth_headers(accountant, ["accountant"])
    assert client.get("/v1/ops/inbox", headers=headers).status_code == 403
    assert (
        client.post(
            f"/v1/ops/inbox/{uuid.uuid4()}/retry",
            headers={**headers, "Idempotency-Key": "retry-denied"},
        ).status_code
        == 403
    )
    assert client.get("/v1/ops/runtime", headers=headers).status_code == 403


def test_ops_inbox_list_and_retry(client: TestClient, make_user, auth_headers, db) -> None:
    admin = make_user(["system_admin"])
    headers = auth_headers(admin, ["system_admin"])
    failed = InboxEvent(
        consumer="worker", event_id=uuid.uuid4(), status=InboxStatus.FAILED, attempts=3
    )
    processed = InboxEvent(
        consumer="worker", event_id=uuid.uuid4(), status=InboxStatus.PROCESSED, attempts=1
    )
    db.add_all([failed, processed])
    db.commit()

    listed = client.get("/v1/ops/inbox?status=failed", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["eventId"] == str(failed.id)
    assert item["consumer"] == "worker"
    assert item["status"] == "failed"
    assert item["attempts"] == 3

    retried = client.post(
        f"/v1/ops/inbox/{failed.id}/retry",
        headers={**headers, "Idempotency-Key": "retry-key-1"},
    )
    assert retried.status_code == 200
    assert retried.json()["eventId"] == str(failed.id)
    assert retried.json()["status"] == "pending"

    replay = client.post(
        f"/v1/ops/inbox/{failed.id}/retry",
        headers={**headers, "Idempotency-Key": "retry-key-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["eventId"] == str(failed.id)

    conflict = client.post(
        f"/v1/ops/inbox/{processed.id}/retry",
        headers={**headers, "Idempotency-Key": "retry-key-1"},
    )
    assert conflict.status_code == 409
    _assert_error_envelope(conflict.json(), "idempotency_key_conflict")

    fresh = client.get("/v1/ops/inbox?status=failed", headers=headers)
    assert fresh.json()["total"] == 0


def test_ops_inbox_retry_requires_idempotency_key(
    client: TestClient, make_user, auth_headers, db
) -> None:
    admin = make_user(["system_admin"])
    event = InboxEvent(consumer="worker", event_id=uuid.uuid4(), status=InboxStatus.FAILED)
    db.add(event)
    db.commit()
    response = client.post(
        f"/v1/ops/inbox/{event.id}/retry", headers=auth_headers(admin, ["system_admin"])
    )
    assert response.status_code == 422
    _assert_error_envelope(response.json(), "validation_error")


def test_ops_runtime_shape(client: TestClient, make_user, auth_headers) -> None:
    admin = make_user(["system_admin"])
    response = client.get("/v1/ops/runtime", headers=auth_headers(admin, ["system_admin"]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"worker", "inbox", "effect", "reconciliation"}
    assert "status" in body["worker"]
    assert "status" in body["inbox"]
    assert "status" in body["effect"]
    assert "status" in body["reconciliation"]


# ---------------------------------------------------------------------------
# Reconciliation diff resolve: RBAC + Idempotency-Key
# ---------------------------------------------------------------------------


def _make_manual_diff(db) -> tuple[uuid.UUID, uuid.UUID]:
    run = run_reconciliation(
        db,
        run_type="daily",
        domains=["effect"],
        connectors={
            "effect": lambda _domain: [
                {"entity_type": "effect", "entity_id": "ef-remote-1", "state": "succeeded"}
            ]
        },
    )
    db.commit()
    diff = db.execute(select(ReconciliationDiff)).scalar_one()
    return run.id, diff.id


def test_diff_resolve_rbac(client: TestClient, make_user, auth_headers, db) -> None:
    run_id, diff_id = _make_manual_diff(db)
    warehouse = make_user(["warehouse_staff"])
    denied = client.post(
        f"/v1/reconciliations/{run_id}/diffs/{diff_id}/resolve",
        json={"note": "nope"},
        headers=auth_headers(warehouse, ["warehouse_staff"]),
    )
    assert denied.status_code == 403
    _assert_error_envelope(denied.json(), "permission_denied")


def test_diff_resolve_idempotent_replay_and_conflict(
    client: TestClient, make_user, auth_headers, db
) -> None:
    run_id, diff_id = _make_manual_diff(db)
    accountant = make_user(["accountant"])
    headers = {
        **auth_headers(accountant, ["accountant"]),
        "Idempotency-Key": "resolve-key-1",
    }
    url = f"/v1/reconciliations/{run_id}/diffs/{diff_id}/resolve"
    first = client.post(url, json={"note": "manual fix"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["diffId"] == str(diff_id)
    replay = client.post(url, json={"note": "manual fix"}, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["diffId"] == str(diff_id)
    conflict = client.post(url, json={"note": "different note"}, headers=headers)
    assert conflict.status_code == 409
    _assert_error_envelope(conflict.json(), "idempotency_key_conflict")


# ---------------------------------------------------------------------------
# Workflow detail normalization (计划 §四.1)
# ---------------------------------------------------------------------------


def test_workflow_detail_normalized_fields(
    client: TestClient, make_user, auth_headers, db
) -> None:
    owner = make_user(["catalog_owner"])
    auth = auth_headers(owner, ["catalog_owner"])
    workflow_id = _seed_catalog_revision(db, owner, "SKU-NORM")
    detail = client.get(f"/v1/workflows/{workflow_id}", headers=auth).json()
    assert detail["events"] and all("type" in event for event in detail["events"])
    assert isinstance(detail["effects"], list)
    item = detail["workItems"][0]
    assert item["workflowId"] == workflow_id
    assert item["createdAt"]
    assert item["expectedWorkflowVersion"] == 1
    assert item["expectedVersion"] == 1  # legacy compatibility field
    # Effect normalization fields are present whenever effects exist; assert
    # the shape on an approval that records an effect.
    approver = make_user(["catalog_owner", "budget_owner"])
    approved = client.post(
        f"/v1/work-items/{item['workItemId']}/decisions",
        json={"decision": "approve", "expectedWorkflowVersion": item["expectedWorkflowVersion"]},
        headers={**auth_headers(approver, ["catalog_owner"]), "Idempotency-Key": "norm-approve-1"},
    )
    assert approved.status_code == 200
    detail2 = client.get(f"/v1/workflows/{workflow_id}", headers=auth).json()
    assert detail2["effects"]
    effect = detail2["effects"][0]
    assert "remoteReference" in effect
    assert "attempt" in effect
    assert "errorDetail" in effect


# ---------------------------------------------------------------------------
# Audit on RBAC denials (no token / body / PII)
# ---------------------------------------------------------------------------


def test_rbac_denial_writes_audit_without_pii(
    client: TestClient, make_user, auth_headers, db
) -> None:
    denied_user = make_user(["customer_service"])
    response = client.post(
        "/v1/catalog-revisions",
        json={"sku": "SKU-AUDIT"},
        headers={
            **auth_headers(denied_user, ["customer_service"]),
            "Idempotency-Key": "audit-key",
        },
    )
    assert response.status_code == 403
    rows = (
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "rbac.denied",
                AuditLog.actor_user_id == denied_user,
            )
        )
        .scalars()
        .all()
    )
    assert rows
    changes = rows[0].changes or {}
    assert changes.get("required_roles") == ["catalog_owner"]
    assert "token" not in changes
    assert "payload" not in changes
    assert "email" not in changes
