"""P5 模拟：对 Shopify 订单 #1001 执行整单退货退款闭环（无真实客户）。

数据流（全部真实系统，数据为模拟）：
  dispatch_command(return) → ReturnCase(requested) → 审批链：
  eligibility(customer_service) → 收货确认(warehouse_staff) → 质检/处置(warehouse_staff)
  → 贷项通知单(accountant) → 退款金额(finance_approver, 四眼) → refund_pending
  → worker 执行：Odoo credit note（真实 JSON-2）+ Shopify refundCreate（开发店手动网关）
  → effect ledger planned→dispatched→succeeded → refund_succeeded → reconciled → closed
  → 对账（shopify/effect 域）。

幂等：按 return_ref 前缀 RET-SIM- 检测已存在则跳过（可传 --force 重放）。
用法：uv run python scripts/simulate_return_refund.py
"""
# ruff: noqa: E402  # 必须先读 .env 再导入 app 模块（Settings 校验）

from __future__ import annotations

import os
import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file = ROOT / "backend" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.uuid7 import uuid7
from app.models.identity import Role, RoleAssignment, User
from app.models.returns import ReturnCase
from app.models.workflow import WorkflowRun, WorkItem, WorkItemStatus
from app.services.commands import _complete_run, advance_entity, dispatch_command
from app.services.effect_ledger import mark_effect, record_effect
from app.services.reconciliation import run_reconciliation
from app.services.work_items import submit_decision

SIM_USERS = [
    ("sim.cs@corp.local", "Sim CS", Role.CUSTOMER_SERVICE),
    ("sim.cs2@corp.local", "Sim CS2", Role.CUSTOMER_SERVICE),
    ("sim.wh@corp.local", "Sim WH", Role.WAREHOUSE_STAFF),
    ("sim.acc@corp.local", "Sim ACC", Role.ACCOUNTANT),
    ("sim.fin@corp.local", "Sim FIN", Role.FINANCE_APPROVER),
]


def ensure_users(db) -> dict[str, list[User]]:
    out: dict[str, list[User]] = {}
    for email, name, role in SIM_USERS:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, display_name=name, is_active=True)
            db.add(user)
            db.flush()
            db.add(RoleAssignment(user_id=user.id, role=role, scope="*"))
        out.setdefault(role.value, []).append(user)
    return out


def main() -> None:
    engine = create_engine(os.environ["COMMERCE_DATABASE_URL"])
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        users = ensure_users(db)
        cs = users["customer_service"][0]
        fin = users["finance_approver"][0]

        from app.connectors.odoo import OdooConnector
        from app.connectors.shopify import ShopifyConnector

        odoo_key = (
            pathlib.Path(r"C:\Users\metra\Documents\Codex\2026-08-09\zhi-x\work\odoo_key.txt")
            .read_text(encoding="utf-8")
            .strip()
        )
        os.environ["COMMERCE_ODOO_BASE_URL"] = "http://localhost:8069"
        os.environ["COMMERCE_ODOO_API_KEY"] = odoo_key
        os.environ["COMMERCE_ODOO_DB"] = "odoo"
        os.environ["COMMERCE_ODOO_USERNAME"] = "admin"
        odoo = OdooConnector()
        shop = ShopifyConnector()

        # 1) 自建一笔测试订单（REST，write_orders；SKU-YIFU-01 变体）
        order_arg = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
        if order_arg:
            shopify_order_id = order_arg
            print("using provided order:", shopify_order_id)
        else:
            tok = shop.exchange_client_credentials_token()
            orders_url = "https://metratio.myshopify.com/admin/api/2026-07/orders.json"
            resp = shop._get_client().post(
                orders_url,
                json={
                    "order": {
                        "line_items": [{"variant_id": 45805093945391, "quantity": 1}],
                        # 带支付交易（capture）建单，订单才有支付链可退款
                        "transactions": [{"kind": "capture", "amount": 99.00, "gateway": "manual"}],
                    }
                },
                headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
            )
            if resp.status_code != 201:
                raise RuntimeError(
                    f"REST order create failed: {resp.status_code} {resp.text[:200]}"
                )
            shopify_order_id = str(resp.json()["order"]["id"])
            print("created test order:", shopify_order_id)

        existing = (
            db.execute(select(ReturnCase).where(ReturnCase.shopify_order_id == shopify_order_id))
            .scalars()
            .first()
        )
        if existing is not None and "--force" not in sys.argv:
            print(f"SKIP: 该订单已有退货 {existing.return_ref}（status={existing.status.value}）")
            odoo.close()
            shop.close()
            return

        # 订单号（name 如 #1003）与 line item
        orders, _ = shop.list_orders(updated_after=None, first=10)
        order = next((o for o in orders if o.get("legacyResourceId") == shopify_order_id), None)
        if order is None:
            raise RuntimeError(f"order {shopify_order_id} not found in Shopify")
        order_ref = order.get("name")
        line_edges = order.get("lineItems", {}).get("edges", [])
        line_item_gid = line_edges[0]["node"]["id"] if line_edges else None
        total = order.get("totalPriceSet", {}).get("presentmentMoney", {}).get("amount", "99.00")
        currency = (
            order.get("totalPriceSet", {}).get("presentmentMoney", {}).get("currencyCode", "JPY")
        )
        print(f"order {order_ref} total {total} {currency} lineItem {line_item_gid}")

        payload = {
            "return_ref": f"RET-SIM-{uuid7()}",
            "shopify_order_id": shopify_order_id,
            "order_ref": order_ref,
            "customer_ref": "SIM-CUSTOMER-01",
            "reason": "quality issue (simulated)",
            "refund_amount": str(total),
            "currency": currency,
        }
        result = dispatch_command(
            db,
            scope="return",
            key=str(uuid.uuid4()),
            command_type="return",
            payload=payload,
            actor_user_id=cs.id,
            correlation_id=str(uuid7()),
        )
        db.commit()
        run = db.get(WorkflowRun, uuid.UUID(result["workflowId"]))
        print("return dispatched:", result["workflowId"], "status", run.status.value)

        # 审批链驱动：每轮取该工作流最新 pending work item，用匹配角色 approve
        guard = 0
        while guard < 20:
            guard += 1
            item = (
                db.execute(
                    select(WorkItem)
                    .where(
                        WorkItem.workflow_id == run.id,
                        WorkItem.status == WorkItemStatus.PENDING,
                    )
                    .order_by(WorkItem.created_at)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if item is None:
                break
            roles = item.required_roles or []
            candidates = [u for r in roles for u in users.get(r, [])]
            proposed = (item.payload_json or {}).get("proposed_by_user_id")
            # 四眼：优先选非提出者
            approver = next(
                (u for u in candidates if str(u.id) != str(proposed)),
                candidates[0] if candidates else None,
            )
            if approver is None:
                raise RuntimeError(f"no sim user for roles {roles}")
            dec = submit_decision(
                db,
                work_item_id=item.id,
                user_id=approver.id,
                decision="approve",
                reason="simulated approval",
                expected_workflow_version=run.version,
            )
            db.commit()
            print("  gate approved:", item.title, "->", dec.get("status"))

        db.refresh(run)
        case = (
            db.execute(select(ReturnCase).where(ReturnCase.return_ref == payload["return_ref"]))
            .scalars()
            .first()
        )
        print("after gates: case status", case.status.value, "| run", run.status.value)
        if case.status.value != "refund_pending":
            raise RuntimeError(f"unexpected case status after gates: {case.status.value}")

        # 1) Odoo credit note（对发票 INV/2026/00001 = account.move id 1 作冲销）
        cn_res = odoo.create_credit_note(
            {
                "partner_id": 6,
                "ref": f"{case.return_ref}",
                "invoice_origin": "S00001",
                "invoice_line_ids": [(0, 0, {"product_id": 5, "quantity": 1, "price_unit": 99.0})],
            },
            idempotency_key=f"return:{case.return_ref}:credit_note_create",
        )
        print("odoo credit note create:", cn_res)
        if not cn_res.ok:
            raise RuntimeError(f"odoo credit note failed: {cn_res.error}")
        cn_id = int(cn_res.remote_reference)
        cn_post = odoo.validate_credit_note(
            cn_id, idempotency_key=f"return:{case.return_ref}:cn_post"
        )
        print("odoo credit note post:", cn_post)
        case.credit_note_id = str(cn_id)

        # 记录 Odoo credit note effects
        for op, ref in (
            ("odoo.credit_note_create", str(cn_id)),
            ("odoo.credit_note_validate", str(cn_id)),
        ):
            eff = record_effect(
                db,
                target_system="odoo",
                operation=op.split(".")[1],
                idempotency_key=f"return:{case.return_ref}:{op}",
                approval_ref=run.id,
            )
            ctx = {"invoice_posted": True}  # 发票 INV/2026/00001 已过账
            mark_effect(db, eff.intent_id, status="dispatched", context=ctx)
            mark_effect(db, eff.intent_id, status="succeeded", remote_reference=ref, context=ctx)
        db.commit()

        # 2) Shopify refund（开发店；REST 建单无支付链，cash 网关免父交易）
        # 找父支付交易（SALE/CAPTURE 成功）作为退款 parentId
        parent_txn = None
        q_txn = """query OrderTx($id: ID!) { order(id: $id) {
          transactions(first: 10) { id kind status }
        } }"""
        tx_payload = shop._graphql(
            q_txn, {"id": f"gid://shopify/Order/{shopify_order_id}"}, operation="order_transactions"
        )
        for node in tx_payload.get("data", {}).get("order", {}).get("transactions") or []:
            if node.get("kind") in ("SALE", "CAPTURE") and node.get("status") == "SUCCESS":
                parent_txn = node.get("id")
                break
        print("parent payment transaction:", parent_txn)
        refund = shop.create_refund(
            f"gid://shopify/Order/{shopify_order_id}",
            amount=str(total),
            note=f"simulated return {case.return_ref}",
            refund_line_items=[{"lineItemId": line_item_gid, "quantity": 1}]
            if line_item_gid
            else None,
            parent_transaction_id=parent_txn,
            # 有父交易用 manual（真实形态）；无父交易时 cash 网关兜底（开发店模拟）。
            gateway="manual" if parent_txn else "cash",
            idempotency_key=f"return:{case.return_ref}:shopify.refund_create",
            allow_real_money=True,
        )
        print("shopify refund:", refund)
        if not refund.ok:
            raise RuntimeError(f"shopify refund failed: {refund.error}")
        case.shopify_refund_gid = refund.remote_reference

        # 记录 shopify.refund_create effect（工作流已 planned 一条，标记它；无则新记）
        from app.models.effect import EffectLedgerEntry

        eff = (
            db.execute(
                select(EffectLedgerEntry).where(
                    EffectLedgerEntry.approval_ref == run.id,
                    EffectLedgerEntry.operation == "refund_create",
                    EffectLedgerEntry.target_system == "shopify",
                )
            )
            .scalars()
            .first()
        )
        if eff is None:
            eff = record_effect(
                db,
                target_system="shopify",
                operation="refund_create",
                idempotency_key=f"return:{case.return_ref}:shopify.refund_create",
                approval_ref=run.id,
            )
        mark_effect(db, eff.intent_id, status="dispatched")
        mark_effect(
            db,
            eff.intent_id,
            status="succeeded",
            remote_reference=refund.remote_reference,
        )
        db.commit()

        # 3) 推进 case 到 closed
        for state in ("refund_succeeded", "reconciled", "closed"):
            advance_entity(
                db,
                case,
                "ReturnCase",
                state,
                correlation_id=run.correlation_id,
                context={"auto": False},
                actor_user_id=fin.id,
            )
            db.commit()
            print("  case ->", state)

        _complete_run(
            db,
            run,
            extras={
                "returnRef": case.return_ref,
                "creditNoteId": case.credit_note_id,
                "shopifyRefundGid": case.shopify_refund_gid,
            },
        )
        db.commit()

        # 4) 对账（不自动抹平）
        rec = run_reconciliation(db, run_type="return-refund-sim", domains=["shopify", "effect"])
        db.commit()
        print(
            "reconciliation:", rec.id, rec.status.value, "diffs:", (rec.summary or {}).get("diffs")
        )

        db.refresh(case)
        print(
            "DONE: case",
            case.return_ref,
            "->",
            case.status.value,
            "| CN",
            case.credit_note_id,
            "| refund",
            case.shopify_refund_gid,
        )
        odoo.close()
        shop.close()
    finally:
        db.close()


if __name__ == "__main__":
    main()
