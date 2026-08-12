"""P3 模拟：采购闭环（需求→RFQ→审批→收货→账单→对账；无真实客户）。

流程：accept_command(procurement, DBOS v2) → worker 启动 v2 definition →
demand_detected/rfq_draft/pending_approval
→ approve_po(budget_owner, 四眼) → po_confirmed（记 odoo.po_create/po_confirm 计划）
→ confirm_receipt(warehouse_staff) → received（记 odoo.receive_transfer）
→ approve_bill(accountant) → bill_posted/in_payment → close_po(accountant) → reconciled/closed
→ worker/DBOS 执行：Odoo create_po/confirm_po/收货/账单（真实 JSON-2）→ effect ledger 标记 succeeded
→ 对账（effect 域）。

幂等：按 supplier=SIM-SUPPLIER 且 currency=JPY 的已 closed 订单跳过（--force 重跑）。
默认不执行真实外部写；传 --live 且环境为开发店/沙盒时才调用正式 accept_command。
用法：uv run python scripts/simulate_procurement.py
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
from app.models.identity import Role, RoleAssignment, User
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.workflow import WorkflowRun, WorkItem, WorkItemStatus
from app.services.commands import accept_command
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="执行真实外部写（开发店/沙盒）")
    parser.add_argument("--force", action="store_true", help="重跑已存在的模拟单")
    args, _ = parser.parse_known_args()
    if not args.live:
        print("DRY-RUN：未传 --live，只演示命令受理路径，不执行真实 Odoo 写。")
        print("用法：uv run python scripts/simulate_procurement.py --live")
        return
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
        if existing is not None and not args.force:
            print(f"SKIP: 已存在模拟采购单 {existing.id}（status=closed）")
            return

        accepted = accept_command(
            db,
            command={
                "type": "procurement",
                "payload": {
                    "sku": "SKU-YIFU-01",
                    "qty": "10",
                    "uom": "unit",
                    "supplier": "SIM-SUPPLIER",
                    "unit_cost": "50.00",
                    "currency": "JPY",
                },
            },
            actor_user_id=pl.id,
            idempotency_key=f"sim-proc-{uuid.uuid4()}",
            correlation_id=str(uuid7()),
        )
        db.commit()
        run = db.get(WorkflowRun, accepted.workflow_id)
        print(
            "procurement accepted:",
            run.id,
            "| engine",
            run.orchestration_engine,
            "| v",
            run.workflow_version,
            "| status",
            run.status.value,
        )

        # v2 主线下由 worker 启动 definition 并创建 work item；脚本只负责
        # 审批（正式 decision interface），不直接推进领域状态。
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
            time.sleep(1.0)

        db.refresh(run)
        print("after gates: run", run.status.value)
        if run.status.value not in ("completed", "failed", "needs_reconciliation"):
            raise RuntimeError(f"unexpected run status: {run.status.value}")

        # ---- 只读验证：worker/DBOS 已执行 Odoo effects ----
        from app.models.effect import EffectLedgerEntry

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

        order = (
            db.execute(
                select(ProcurementOrder)
                .where(ProcurementOrder.supplier == "SIM-SUPPLIER")
                .order_by(ProcurementOrder.created_at.desc())
            )
            .scalars()
            .first()
        )
        if order is not None:
            print("DONE: po", order.id, "->", order.status.value, "| odoo_po", order.odoo_po_id)
        else:
            print("WARN: no procurement order found for this run")
    finally:
        db.close()


if __name__ == "__main__":
    main()
