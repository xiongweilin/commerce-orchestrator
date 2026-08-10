"""P3 模拟：采购闭环（需求→RFQ→审批→收货→账单→对账；无真实客户）。

流程：dispatch_command(procurement) → demand_detected/rfq_draft/pending_approval
→ approve_po(budget_owner, 四眼) → po_confirmed（记 odoo.po_create/po_confirm 计划）
→ confirm_receipt(warehouse_staff) → received（记 odoo.receive_transfer）
→ approve_bill(accountant) → bill_posted/in_payment → close_po(accountant) → reconciled/closed
→ worker 执行：Odoo create_po/confirm_po/收货/账单（真实 JSON-2）→ effect ledger 标记 succeeded
→ 对账（effect 域）。

幂等：按 supplier=SIM-SUPPLIER 且 currency=JPY 的已 closed 订单跳过（--force 重跑）。
用法：uv run python scripts/simulate_procurement.py
"""
# ruff: noqa: E402  # 必须先读 .env 再导入 app 模块（Settings 校验）

from __future__ import annotations

import os
import pathlib
import sys
import uuid
from datetime import date

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
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.workflow import WorkflowRun, WorkItem, WorkItemStatus
from app.services.commands import dispatch_command
from app.services.effect_ledger import mark_effect, record_effect
from app.services.reconciliation import run_reconciliation
from app.services.work_items import submit_decision

SIM_USERS = [
    ("sim.pl@corp.local", "Sim PL", Role.PROCUREMENT_LEAD),
    ("sim.bo@corp.local", "Sim BO", Role.BUDGET_OWNER),
    ("sim.wh2@corp.local", "Sim WH2", Role.WAREHOUSE_STAFF),
    ("sim.acc2@corp.local", "Sim ACC2", Role.ACCOUNTANT),
    ("sim.acc3@corp.local", "Sim ACC3", Role.ACCOUNTANT),
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
        pl = users["procurement_lead"][0]

        existing = (
            db.execute(
                select(ProcurementOrder).where(
                    ProcurementOrder.supplier == "SIM-SUPPLIER",
                    ProcurementOrder.currency == "JPY",
                    ProcurementOrder.status == ProcurementStatus.CLOSED,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None and "--force" not in sys.argv:
            print(f"SKIP: 已存在模拟采购单 {existing.id}（status=closed）")
            return

        result = dispatch_command(
            db,
            scope="procurement",
            key=str(uuid.uuid4()),
            command_type="procurement",
            payload={
                "sku": "SKU-YIFU-01",
                "qty": "10",
                "uom": "unit",
                "supplier": "SIM-SUPPLIER",
                "unit_cost": "50.00",
                "currency": "JPY",
            },
            actor_user_id=pl.id,
            correlation_id=str(uuid7()),
        )
        db.commit()
        run = db.get(WorkflowRun, uuid.UUID(result["workflowId"]))
        order = (
            db.execute(
                select(ProcurementOrder).where(ProcurementOrder.id == uuid.UUID(result["poId"]))
            )
            .scalars()
            .first()
        )
        print("procurement dispatched:", run.id, "| po", order.id, "| status", run.status.value)

        # 审批链（四眼：approver != proposer）
        guard = 0
        while guard < 12:
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

        db.refresh(order)
        print("after gates: po status", order.status.value, "| run", run.status.value)
        if order.status.value != "closed":
            raise RuntimeError(f"unexpected po status: {order.status.value}")

        # ---- worker 执行段（真实 Odoo）----
        odoo_key = (
            pathlib.Path(r"C:\Users\metra\Documents\Codex\2026-08-09\zhi-x\work\odoo_key.txt")
            .read_text(encoding="utf-8")
            .strip()
        )
        os.environ["COMMERCE_ODOO_BASE_URL"] = "http://localhost:8069"
        os.environ["COMMERCE_ODOO_API_KEY"] = odoo_key
        os.environ["COMMERCE_ODOO_DB"] = "odoo"
        os.environ["COMMERCE_ODOO_USERNAME"] = "admin"
        from app.connectors.odoo import OdooConnector

        odoo = OdooConnector()
        po_res = odoo.create_po(
            {
                "partner_id": 6,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": 5,
                            "name": "衣服 SKU-YIFU-01",
                            "product_qty": 10.0,
                            "price_unit": 50.0,
                        },
                    )
                ],
            },
            idempotency_key=f"procurement:{run.id}:po_create",
        )
        print("odoo po create:", po_res)
        if not po_res.ok:
            raise RuntimeError(f"odoo po failed: {po_res.error}")
        po_id = int(po_res.remote_reference)
        po_confirm = odoo.confirm_po(po_id, idempotency_key=f"procurement:{run.id}:po_confirm")
        print("odoo po confirm:", po_confirm)
        order.odoo_po_id = str(po_id)
        db.commit()

        # 标记 po_create/po_confirm effect
        from app.models.effect import EffectLedgerEntry

        for op in ("po_create", "po_confirm"):
            eff = (
                db.execute(
                    select(EffectLedgerEntry).where(
                        EffectLedgerEntry.approval_ref == run.id,
                        EffectLedgerEntry.operation == op,
                    )
                )
                .scalars()
                .first()
            )
            if eff is None:
                eff = record_effect(
                    db,
                    target_system="odoo",
                    operation=op,
                    idempotency_key=f"procurement:{run.id}:{op}",
                    approval_ref=run.id,
                )
            mark_effect(db, eff.intent_id, status="dispatched")
            mark_effect(db, eff.intent_id, status="succeeded", remote_reference=str(po_id))
        db.commit()

        # Odoo 收货：PO 确认后由 purchase_stock 自动生成收据 picking，验证它
        from app.connectors.odoo import OdooConnector as _OC

        odoo2 = _OC()
        pickings = odoo2._post(
            "stock.picking",
            "search_read",
            {
                "domain": [["purchase_id", "=", po_id]],
                "fields": ["id", "state", "origin"],
                "limit": 5,
            },
        )
        picking_id = pickings[0]["id"] if pickings else None
        receive = None
        if picking_id:
            receive = odoo2.validate_picking(
                picking_id, idempotency_key=f"procurement:{run.id}:receive"
            )
            print("odoo receive picking:", picking_id, receive)
        else:
            print("no picking found for PO; receive effect marked as simulated")
        recv_eff = (
            db.execute(
                select(EffectLedgerEntry).where(
                    EffectLedgerEntry.approval_ref == run.id,
                    EffectLedgerEntry.operation == "receive_transfer",
                )
            )
            .scalars()
            .first()
        )
        if recv_eff is None:
            recv_eff = record_effect(
                db,
                target_system="odoo",
                operation="receive_transfer",
                idempotency_key=f"procurement:{run.id}:receive_transfer",
                approval_ref=run.id,
            )
        inv_ctx = {"inventory_change_source": "stock_move"}
        mark_effect(db, recv_eff.intent_id, status="dispatched", context=inv_ctx)
        if receive is not None and receive.ok:
            mark_effect(
                db,
                recv_eff.intent_id,
                status="succeeded",
                remote_reference=str(picking_id),
                context=inv_ctx,
            )
        else:
            mark_effect(
                db,
                recv_eff.intent_id,
                status="failed",
                error_detail="no Odoo picking available for PO receipt (simulated)",
                context=inv_ctx,
            )
        db.commit()

        # Odoo 账单（account.move in_invoice）并过账
        bill = odoo2.create_bill(
            {
                "partner_id": 6,
                "ref": f"PO-{po_id}-BILL",
                "invoice_date": date.today().isoformat(),
                "invoice_line_ids": [
                    (0, 0, {"product_id": 5, "quantity": 10.0, "price_unit": 50.0})
                ],
            },
            idempotency_key=f"procurement:{run.id}:bill_create",
        )
        print("odoo bill create:", bill)
        if bill.ok:
            bill_id = int(bill.remote_reference)
            bill_post = odoo2.validate_invoice(
                bill_id, idempotency_key=f"procurement:{run.id}:bill_post"
            )
            print("odoo bill post:", bill_post)
            bill_eff = record_effect(
                db,
                target_system="odoo",
                operation="bill_create",
                idempotency_key=f"procurement:{run.id}:bill_create",
                approval_ref=run.id,
            )
            mark_effect(db, bill_eff.intent_id, status="dispatched")
            mark_effect(db, bill_eff.intent_id, status="succeeded", remote_reference=str(bill_id))
        db.commit()
        odoo.close()
        odoo2.close()

        # 对账（effect 域，不自动抹平）
        rec = run_reconciliation(db, run_type="procurement-sim", domains=["effect"])
        db.commit()
        print(
            "reconciliation:", rec.id, rec.status.value, "diffs:", (rec.summary or {}).get("diffs")
        )
        db.refresh(order)
        print("DONE: po", order.id, "->", order.status.value, "| odoo_po", order.odoo_po_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
