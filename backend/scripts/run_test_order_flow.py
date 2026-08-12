"""One-shot Shopify -> O2C driver for the commerce-orchestrator backend.

Drives a real Shopify order through the local order-to-cash pipeline:

1. publishes the product to the Online Store publication and verifies
   ``isPublished``;
2. ingests the order as if a ``orders/create`` webhook arrived (creates the
   SalesOrder, the order-to-cash WorkflowRun and the ``order.received``
   event) -- Shopify does not push webhooks for API-created orders, so the
   payload is built from the live order read back from the Admin API;
3. advances the O2C state machine ``received -> validated -> accepted ->
   odo_drafted -> confirmed -> reserved -> picking -> shipped -> invoiced ->
   in_payment -> reconciled -> closed`` through the approval service (work
   items for the human gates, four-eyes proposer/approver separation);
   Odoo/Shopify effects are recorded ``planned`` in the effect ledger
   (execution belongs to the Odoo agent / worker);
4. writes the variant inventory projection (owner ``shopify_inventory``);
5. runs a ``shopify`` reconciliation comparing the local SalesOrder with the
   live Shopify order (diffs land in MANUAL_RECONCILIATION, never auto-resolved).

Idempotent: keyed on the Shopify order id.  A re-run resolves the existing
SalesOrder + WorkflowRun, replays planned effects, drives any remaining gates
and re-runs the reconciliation.

Usage (from the repository root or ``backend/``):

    python scripts/run_test_order_flow.py [SHOPIFY_ORDER_ID]
    # e.g. python scripts/run_test_order_flow.py 6859791728687

The repo-root ``.env`` is loaded automatically, so the script works from any
working directory.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"

SHOPIFY_ORDER_ID = "6859791728687"
ORDER_REF = "#1001"
WEBHOOK_TOPIC = "orders/create"
PRODUCT_GID = "gid://shopify/Product/8416672907311"
VARIANT_GID = "gid://shopify/ProductVariant/45805093945391"
PUBLICATION_GID = "gid://shopify/Publication/208562651183"
PRODUCT_SKU = "SKU-YIFU-01"

# (next_step payload name, approver user key) for the O2C human gates.
O2C_GATES = (
    ("reserve", "inventory"),
    ("ship", "warehouse"),
    ("invoice", "accountant1"),
    ("close", "accountant2"),
)

# user key -> (email, display name, roles)
O2C_USERS = {
    "proposer": ("o2c.proposer@x.com", "O2C Proposer", ["commerce_lead"]),
    "inventory": ("o2c.inventory@x.com", "O2C Inventory Supervisor", ["inventory_supervisor"]),
    "warehouse": ("o2c.warehouse@x.com", "O2C Warehouse Staff", ["warehouse_staff"]),
    "accountant1": ("o2c.accountant1@x.com", "O2C Accountant 1", ["accountant"]),
    "accountant2": ("o2c.accountant2@x.com", "O2C Accountant 2", ["accountant"]),
}


def _load_env() -> None:
    """Load the repo-root .env into the process env (no overwrite)."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_fallback_payload(shopify_order_id: str) -> dict[str, Any]:
    """Known-good payload for #1001 when the live order cannot be read."""
    return {
        "id": int(shopify_order_id),
        "name": ORDER_REF,
        "total_price": "99.00",
        "currency": "JPY",
        "line_items": [
            {
                "id": 16175057109039,
                "title": "衣服",
                "sku": PRODUCT_SKU,
                "quantity": 1,
                "variant_id": 45805093945391,
                "price": "99.00",
            }
        ],
        "customer": {"email": "test-customer@example.com"},
        "shipping_address": {"country": "JP", "city": "Tokyo"},
    }


def fetch_shopify_order_payload(connector: Any, shopify_order_id: str) -> dict[str, Any] | None:
    """Read the live order back from Shopify and shape it as a webhook payload."""
    orders, _cursor = connector.list_orders(None, first=100)
    for order in orders:
        if str(order.get("legacyResourceId")) != str(shopify_order_id):
            continue
        money = (order.get("totalPriceSet") or {}).get("presentmentMoney") or {}
        line_items = [
            {
                "id": node.get("id"),
                "title": node.get("title"),
                "sku": node.get("sku"),
                "quantity": node.get("quantity"),
            }
            for node in (
                (edge.get("node") or {})
                for edge in ((order.get("lineItems") or {}).get("edges") or [])
            )
        ]
        return {
            "id": int(shopify_order_id),
            "name": order.get("name"),
            "total_price": money.get("amount") or "0",
            "currency": money.get("currencyCode") or "JPY",
            "line_items": line_items,
            "customer": {"email": f"customer-{shopify_order_id}@example.com"},
            "shipping_address": {},
        }
    return None


def ensure_users(db) -> dict[str, uuid.UUID]:
    """Create the O2C test users + role assignments (idempotent)."""
    from sqlalchemy import select

    from app.models.identity import Role, RoleAssignment, User

    result: dict[str, uuid.UUID] = {}
    for key, (email, display_name, roles) in O2C_USERS.items():
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=display_name, is_active=True)
            db.add(user)
            db.flush()
        for role in roles:
            assignment = db.execute(
                select(RoleAssignment).where(
                    RoleAssignment.user_id == user.id,
                    RoleAssignment.role == Role(role),
                    RoleAssignment.scope == "*",
                )
            ).scalar_one_or_none()
            if assignment is None:
                db.add(RoleAssignment(user_id=user.id, role=Role(role), scope="*"))
        db.flush()
        result[key] = user.id
    return result


def resolve_order_and_run(db, payload: dict[str, Any], webhook_id: str) -> tuple[Any, Any, bool]:
    """Idempotently resolve/create the SalesOrder + order-to-cash run.

    Returns ``(order, run, created_or_adopted)``.  The ingest service path is
    used for a fresh order; a stale fixture with the same ``order_ref`` but a
    different Shopify id is adopted in place (the real data wins); re-runs
    resolve the existing row and run.
    """
    from sqlalchemy import select

    from app.core.security import encrypt_payload
    from app.core.time import utc_now
    from app.models.order import SalesOrder
    from app.models.projections import Projection
    from app.models.workflow import WorkflowRun
    from app.services.webhooks import ingest_shopify_webhook

    shopify_id = str(payload["id"])
    order = db.execute(
        select(SalesOrder).where(SalesOrder.shopify_order_id == shopify_id)
    ).scalar_one_or_none()
    created_or_adopted = False

    if order is None:
        by_ref = db.execute(
            select(SalesOrder).where(SalesOrder.order_ref == str(payload.get("name")))
        ).scalar_one_or_none()
        if by_ref is not None:
            # stale fixture with a different (synthetic) Shopify id: adopt the
            # real order data in place so the unique order_ref is preserved.
            order = by_ref
            order.shopify_order_id = shopify_id
            order.total = Decimal(str(payload.get("total_price") or "0"))
            order.currency = str(payload.get("currency") or "JPY")
            order.items = payload.get("line_items") or []
            order.shipping = payload.get("shipping_address")
            created_or_adopted = True
            raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            encrypted = base64.b64encode(encrypt_payload(raw_body)).decode("ascii")
            db.add(
                Projection(
                    owner="shopify_webhook",
                    source=WEBHOOK_TOPIC,
                    external_id=webhook_id,
                    observed_at=utc_now(),
                    payload={
                        "enc": encrypted,
                        "headers": {"x-shopify-topic": WEBHOOK_TOPIC},
                        "topic": WEBHOOK_TOPIC,
                    },
                )
            )
        else:
            ingest_shopify_webhook(
                db,
                webhook_id=webhook_id,
                topic=WEBHOOK_TOPIC,
                raw_body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                payload=payload,
                headers={"x-shopify-topic": WEBHOOK_TOPIC},
            )
            created_or_adopted = True
            order = db.execute(
                select(SalesOrder).where(SalesOrder.shopify_order_id == shopify_id)
            ).scalar_one()

    runs = (
        db.execute(select(WorkflowRun).where(WorkflowRun.workflow_type == "order-to-cash"))
        .scalars()
        .all()
    )
    run = next(
        (
            r
            for r in runs
            if str(
                (r.input_json or {}).get("id")
                or (r.input_json or {}).get("shopify_order_id")
                or ""
            )
            == shopify_id
        ),
        None,
    )
    if run is None:
        raise RuntimeError(
            "no DBOS v2 order-to-cash run found after webhook ingest; "
            "refusing to create a legacy v1 run"
        )
    db.flush()
    return order, run, created_or_adopted


def drive_gates_v2(db, run: Any, users: dict[str, uuid.UUID], *, timeout_s: int = 300) -> None:
    """Approve v2 O2C gates as they appear until the run reaches a terminal state.

    The DBOS worker advances the workflow and creates/plans gates and effects
    asynchronously, so this polls pending work items, approves each one
    (committing per decision), and exits when the run is terminal.
    """
    import time

    from sqlalchemy import select

    from app.models.workflow import WorkflowRunStatus, WorkItem, WorkItemStatus
    from app.services.approvals import submit_decision

    deadline = time.monotonic() + timeout_s
    last_error: str | None = None
    while time.monotonic() < deadline:
        db.refresh(run)
        if run.status in (
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.NEEDS_RECONCILIATION,
        ):
            return
        pending = (
            db.execute(
                select(WorkItem).where(
                    WorkItem.workflow_id == run.id,
                    WorkItem.status == WorkItemStatus.PENDING,
                )
            )
            .scalars()
            .all()
        )
        acted = False
        for item in pending:
            step = (item.payload_json or {}).get("next_step")
            approver_key = dict(O2C_GATES).get(step)
            if approver_key is None:
                continue
            try:
                submit_decision(
                    db,
                    work_item_id=item.id,
                    user_id=users[approver_key],
                    decision="approve",
                    reason=f"run_test_order_flow: approve {step} gate (v2)",
                    expected_workflow_version=item.expected_version,
                )
                db.commit()
                acted = True
            except Exception as exc:  # noqa: BLE001 - retry on the next poll
                last_error = f"{step}: {exc}"
        if not acted:
            time.sleep(2)
    raise RuntimeError(
        f"v2 O2C gates did not finish within {timeout_s}s "
        f"(run={run.status.value}; last_error={last_error})"
    )

def project_inventory(db, connector: Any) -> dict[str, Any]:
    """Write the variant inventory projection (owner ``shopify_inventory``)."""
    from sqlalchemy import select

    from app.core.time import utc_now
    from app.models.projections import Projection

    nodes, _cursor = connector.list_inventory(first=10)
    node = next(
        (n for n in nodes if n.get("variant_id") == VARIANT_GID),
        next((n for n in nodes if n.get("sku") == PRODUCT_SKU), None),
    )
    if node is None:
        raise RuntimeError(f"inventory for variant {VARIANT_GID} not found in Shopify")
    payload = {
        "available": node.get("available"),
        "sku": node.get("sku"),
        "inventory_item_id": node.get("inventory_item_id"),
        "inventory_level_id": node.get("inventory_level_id"),
        "location_id": node.get("location_id"),
        "quantities": node.get("quantities"),
    }
    existing = db.execute(
        select(Projection).where(
            Projection.owner == "shopify_inventory",
            Projection.source == "products",
            Projection.external_id == VARIANT_GID,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            Projection(
                owner="shopify_inventory",
                source="products",
                external_id=VARIANT_GID,
                observed_at=utc_now(),
                payload=payload,
            )
        )
    else:
        existing.payload = payload
        existing.observed_at = utc_now()
    db.flush()
    return payload


def shopify_connector_rows(connector: Any, _domain: str) -> list[dict[str, Any]]:
    """Connector read-state for the ``shopify`` reconciliation domain."""
    rows: list[dict[str, Any]] = []
    orders, _cursor = connector.list_orders(None, first=100)
    for order in orders:
        money = (order.get("totalPriceSet") or {}).get("presentmentMoney") or {}
        rows.append(
            {
                "entity_type": "sales_order",
                "entity_id": order.get("name"),
                "state": order.get("displayFinancialStatus"),
                "shopify_order_id": order.get("legacyResourceId"),
                "total": money.get("amount"),
                "currency": money.get("currencyCode"),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "shopify_order_id",
        nargs="?",
        default=SHOPIFY_ORDER_ID,
        help=f"Shopify order id (default {SHOPIFY_ORDER_ID})",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="执行真实外部写（Shopify 发布 / Odoo 订单；开发店沙盒）",
    )
    args = parser.parse_args()
    if not args.live:
        print("DRY-RUN：未传 --live，不执行真实外部写。")
        print("用法：uv run python scripts/run_test_order_flow.py [ORDER_ID] --live")
        return 0
    _load_env()
    sys.path.insert(0, str(BACKEND_DIR))

    from sqlalchemy import select

    import app.services.commands  # noqa: F401  (registers O2C next steps)
    from app.connectors.shopify import ShopifyConnector
    from app.core.db import SessionLocal
    from app.core.logging import configure_logging
    from app.core.time import utc_now
    from app.models.effect import EffectLedgerEntry
    from app.models.messaging import OutboxEvent
    from app.models.reconciliation import ReconciliationDiff
    from app.models.workflow import WorkItem
    from app.services.commands import canonical_hash
    from app.services.effect_ledger import mark_effect, record_effect
    from app.services.reconciliation import run_reconciliation

    configure_logging()
    connector = ShopifyConnector()
    summary: dict[str, Any] = {}
    try:
        probe = connector.probe()
        summary["probe"] = probe
        if not probe.get("ok"):
            raise RuntimeError(f"Shopify probe failed: {probe}")

        # 1) product publish
        publish_result = connector.publish_product(
            PRODUCT_GID,
            publication_id=PUBLICATION_GID,
            idempotency_key=f"run-test-order-flow:{PRODUCT_GID}",
        )
        publish_status = connector.get_product_publish_status(PRODUCT_GID)
        is_published = bool(
            publish_status
            and publish_status.get("isPublished")
            and PUBLICATION_GID in (publish_status.get("publicationIds") or [])
        )
        summary["publish"] = {
            "result_ok": publish_result.ok,
            "result_status": publish_result.status,
            "result_error": publish_result.error,
            "is_published": is_published,
            "published_at": (publish_status or {}).get("publishedAt"),
        }

        # 2) webhook payload from live order data (fallback to known #1001)
        payload = fetch_shopify_order_payload(connector, args.shopify_order_id)
        if payload is None:
            print(f"live order {args.shopify_order_id} not found; using known #1001 payload")
            payload = build_fallback_payload(args.shopify_order_id)
        webhook_id = str(uuid.uuid4())

        # 3-6) users, ingest/resolve (commit), v2 gate driving,
        #      inventory projection, reconciliation.
        db = SessionLocal()
        try:
            users = ensure_users(db)
            order, run, created_or_adopted = resolve_order_and_run(db, payload, webhook_id)
            if int(run.workflow_version or 0) != 2 or run.orchestration_engine != "dbos":
                raise RuntimeError(
                    f"expected DBOS v2 run, got engine={run.orchestration_engine!r} "
                    f"version={run.workflow_version!r}"
                )
            # Commit the webhook ingest so the worker can start the v2 workflow,
            # then approve gates as the DBOS driver creates them.
            db.commit()
            drive_gates_v2(db, run, users, timeout_s=300)

            inventory = project_inventory(db, connector)

            publish_intent = uuid.uuid5(uuid.NAMESPACE_URL, f"shopify-publish:{PRODUCT_GID}")
            publish_entry = record_effect(
                db,
                intent_id=publish_intent,
                target_system="shopify",
                operation="product_publish",
                idempotency_key=f"run-test-order-flow:{PRODUCT_GID}",
            )
            publish_status_value = publish_entry.status.value
            if publish_status_value == "planned":
                mark_effect(db, publish_intent, status="dispatched")
                mark_effect(
                    db,
                    publish_intent,
                    status="succeeded",
                    remote_reference=(
                        publish_result.remote_reference if publish_result.ok else PUBLICATION_GID
                    ),
                    response_hash=canonical_hash(publish_status or {}),
                )
            elif publish_status_value == "dispatched":
                mark_effect(
                    db,
                    publish_intent,
                    status="succeeded",
                    remote_reference=(
                        publish_result.remote_reference if publish_result.ok else PUBLICATION_GID
                    ),
                    response_hash=canonical_hash(publish_status or {}),
                )

            rec_run = run_reconciliation(
                db,
                run_type="shopify-o2c-flow",
                domains=["shopify"],
                connectors={"shopify": lambda domain: shopify_connector_rows(connector, domain)},
            )
            db.commit()

            # 7) collect evidence
            events = (
                db.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.correlation_id == run.correlation_id)
                    .order_by(OutboxEvent.occurred_at)
                )
                .scalars()
                .all()
            )
            effects = (
                db.execute(
                    select(EffectLedgerEntry).where(EffectLedgerEntry.approval_ref == run.id)
                )
                .scalars()
                .all()
            )
            work_items = (
                db.execute(select(WorkItem).where(WorkItem.workflow_id == run.id)).scalars().all()
            )
            diffs = (
                db.execute(
                    select(ReconciliationDiff).where(ReconciliationDiff.run_id == rec_run.id)
                )
                .scalars()
                .all()
            )

            summary.update(
                {
                    "sales_order": {
                        "id": str(order.id),
                        "order_ref": order.order_ref,
                        "shopify_order_id": order.shopify_order_id,
                        "status": order.status.value,
                        "total": str(order.total),
                        "currency": order.currency,
                        "created_or_adopted": created_or_adopted,
                    },
                    "workflow": {
                        "id": str(run.id),
                        "status": run.status.value,
                        "events": [e.event_type for e in events],
                        "effects": [
                            {
                                "operation": f"{e.target_system}.{e.operation}",
                                "status": e.status.value,
                                "remote_reference": e.remote_reference,
                            }
                            for e in effects
                        ],
                        "work_items": [
                            {
                                "title": w.title,
                                "kind": w.kind.value,
                                "status": w.status.value,
                                "roles": w.required_roles or [],
                            }
                            for w in work_items
                        ],
                    },
                    "inventory_projection": {
                        "available": inventory.get("available"),
                        "owner": "shopify_inventory",
                        "source": "products",
                        "external_id": VARIANT_GID,
                    },
                    "reconciliation": {
                        "run_id": str(rec_run.id),
                        "status": rec_run.status.value,
                        "summary": rec_run.summary,
                        "diffs": [
                            {
                                "domain": d.domain,
                                "entity_type": d.entity_type,
                                "entity_id": d.entity_id,
                                "expected": d.expected,
                                "actual": d.actual,
                                "difference": d.difference,
                                "status": d.status.value,
                            }
                            for d in diffs
                        ],
                    },
                    "webhook": {"webhook_id": webhook_id, "topic": WEBHOOK_TOPIC},
                    "finished_at": utc_now().isoformat(),
                }
            )
        finally:
            db.close()
    finally:
        connector.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if not summary.get("publish", {}).get("is_published"):
        return 1
    if summary.get("sales_order", {}).get("status") != "closed":
        print("ERROR: order did not reach closed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
