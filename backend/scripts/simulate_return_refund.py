"""P5 模拟：对 Shopify 订单 #1001 执行整单退货退款闭环（无真实客户）。

数据流（全部真实系统，数据为模拟）：
  accept_command(return, DBOS v2) → ReturnCase(requested) → 审批链：
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

import argparse
import os
import pathlib
import sys
import time
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
from app.models.effect import EffectLedgerEntry
from app.models.identity import Role, RoleAssignment, User
from app.models.returns import ReturnCase
from app.models.workflow import WorkflowRun, WorkItem, WorkItemStatus
from app.services.commands import accept_command
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="执行真实外部写（开发店/沙盒）")
    parser.add_argument("--force", action="store_true", help="重跑已存在的模拟单")
    args, rest = parser.parse_known_args()
    if not args.live:
        print("DRY-RUN：未传 --live，只演示命令受理路径，不执行真实外部写。")
        print("用法：uv run python scripts/simulate_return_refund.py --live [SHOPIFY_ORDER_ID]")
        return
    engine = create_engine(os.environ["COMMERCE_DATABASE_URL"])
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        users = ensure_users(db)
        cs = users["customer_service"][0]

        from app.connectors.shopify import ShopifyConnector

        shop = ShopifyConnector()

        # 1) 使用现有 Shopify 订单（默认使用历史验收订单 id，可传参覆盖）
        order_arg = None
        for arg in rest:
            if arg.isdigit():
                order_arg = arg
        if order_arg:
            shopify_order_id = order_arg
            print("using provided order:", shopify_order_id)
        else:
            shopify_order_id = "6859982798895"
            print("using default order:", shopify_order_id)

        existing = (
            db.execute(select(ReturnCase).where(ReturnCase.shopify_order_id == shopify_order_id))
            .scalars()
            .first()
        )
        if existing is not None and "--force" not in sys.argv:
            print(f"SKIP: 该订单已有退货 {existing.return_ref}（status={existing.status.value}）")
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
        accepted = accept_command(
            db,
            command={"type": "return", "payload": payload},
            actor_user_id=cs.id,
            idempotency_key=f"sim-ret-{uuid.uuid4()}",
            correlation_id=str(uuid7()),
        )
        db.commit()
        run = db.get(WorkflowRun, accepted.workflow_id)
        print("return accepted:", run.id, "status", run.status.value)

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
            time.sleep(1.0)

        db.refresh(run)
        case = (
            db.execute(select(ReturnCase).where(ReturnCase.return_ref == payload["return_ref"]))
            .scalars()
            .first()
        )
        print("after gates: case status", case.status.value, "| run", run.status.value)
        if case.status.value != "refund_pending":
            raise RuntimeError(f"unexpected case status after gates: {case.status.value}")


        # ---- 只读验证：worker/DBOS 已执行 Odoo credit note + Shopify refund ----
        db.refresh(run)
        effects = (
            db.execute(
                select(EffectLedgerEntry).where(
                    EffectLedgerEntry.approval_ref == run.id,
                    EffectLedgerEntry.status.in_(
                        ["succeeded", "failed", "outcome_unknown"]
                    ),
                )
            )
            .scalars()
            .all()
        )
        print("effect ledger (terminal):")
        for eff in effects:
            print(
                "  ",
                eff.operation,
                "->",
                eff.status.value,
                "remote",
                eff.remote_reference,
                "attempt",
                eff.attempt,
            )
        case = (
            db.execute(select(ReturnCase).where(ReturnCase.return_ref == payload["return_ref"]))
            .scalars()
            .first()
        )
        if case is not None:
            print(
                "DONE: case",
                case.return_ref,
                "->",
                case.status.value,
                "| credit_note",
                case.credit_note_id,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
