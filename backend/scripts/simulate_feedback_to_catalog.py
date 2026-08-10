"""P6 模拟：Feedback → 聚类 → AI 候选 → 商品修订审批（无真实客户）。

流程：创建 3 条脱敏反馈（product_quality/content_accuracy/availability）
→ 聚类（clustered）→ AI 候选 CatalogChangeCandidate（draft→candidate→frozen→scored→official，
AI 仅建议不批准，元数据含 sanitizer/model/prompt/rule 版本与 proposalHash）
→ 反馈项提升为 promoted_to_catalog_change
→ dispatch catalog-revision（SKU-YIFU-01）→ catalog_owner 审批 → 渠道上架 effect planned。

幂等：检测到 model_id="simulated-v1" 的候选即跳过（--force 重跑）。
用法：uv run python scripts/simulate_feedback_to_catalog.py
"""
# ruff: noqa: E402  # 必须先读 .env 再导入 app 模块（Settings 校验）

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import uuid
from datetime import UTC, datetime

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
from app.models.catalog import (
    CatalogCandidateStatus,
    CatalogChangeCandidate,
)
from app.models.feedback import (
    FeedbackCluster,
    FeedbackItem,
    FeedbackStatus,
    FeedbackType,
)
from app.models.identity import Role, RoleAssignment, User
from app.models.workflow import WorkflowRun
from app.services.commands import dispatch_command
from app.services.work_items import submit_decision

SIM_FEEDBACK = [
    {
        "external_ref": "FB-SIM-001",
        "type": FeedbackType.PRODUCT_QUALITY,
        "text": "收到衣服面料摸起来偏硬，洗一次有点起球。",
        "evidence": {"channel": "simulated-reviews", "observed_at": "2026-08-10T00:00:00Z"},
    },
    {
        "external_ref": "FB-SIM-002",
        "type": FeedbackType.CONTENT_ACCURACY,
        "text": "商品描述写的是纯棉，实际吊牌是聚酯纤维，描述不准。",
        "evidence": {"channel": "simulated-qna", "observed_at": "2026-08-10T00:10:00Z"},
    },
    {
        "external_ref": "FB-SIM-003",
        "type": FeedbackType.AVAILABILITY,
        "text": "这件衣服显示有货，但下单后一直不发货。",
        "evidence": {"channel": "simulated-chat", "observed_at": "2026-08-10T00:20:00Z"},
    },
]

PROPOSAL = {
    "sku": "SKU-YIFU-01",
    "title": "衣服（改良版）",
    "category": "Apparel",
    "description": "面料与描述修正建议：更新材质标注，明确混纺比例。",
    "quality_actions": ["recheck fabric spec", "update description materials"],
    "availability_actions": ["align inventory visibility"],
}


def main() -> None:
    engine = create_engine(os.environ["COMMERCE_DATABASE_URL"])
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    try:
        existing = (
            db.execute(
                select(CatalogChangeCandidate).where(
                    CatalogChangeCandidate.model_id == "simulated-v1"
                )
            )
            .scalars()
            .first()
        )
        if existing is not None and "--force" not in sys.argv:
            print(
                f"SKIP: 已存在模拟候选（candidate {existing.id}，status={existing.status.value}）"
            )
            return

        # 1) 反馈摄入
        items = []
        for fb in SIM_FEEDBACK:
            item = FeedbackItem(
                id=uuid.uuid4(),
                external_ref=fb["external_ref"],
                customer_ref="SIM-CUSTOMER-01",
                type=fb["type"],
                sanitized_text=fb["text"],
                evidence=fb["evidence"],
                observed_at=datetime.fromisoformat(
                    fb["evidence"]["observed_at"].replace("Z", "+00:00")
                ).astimezone(UTC),
                source_revision="sim-feedback-v1",
                status=FeedbackStatus.OBSERVED,
            )
            db.add(item)
            db.flush()
            items.append(item)
        print("feedback items:", [i.external_ref for i in items])

        # 2) 聚类
        cluster = FeedbackCluster(
            id=uuid.uuid4(),
            title="衣服品质/描述/库存反馈聚类（模拟）",
            items_json={"item_ids": [str(i.id) for i in items], "count": len(items)},
            status=FeedbackStatus.CLUSTERED.value,
        )
        db.add(cluster)
        db.flush()
        for i in items:
            i.cluster_id = cluster.id
            i.status = FeedbackStatus.CLUSTERED
        print("cluster:", cluster.id, cluster.title)

        # 3) AI 候选（模拟元数据；AI 只建议不批准）
        proposal_json = json.dumps(PROPOSAL, sort_keys=True, ensure_ascii=False).encode()
        candidate = CatalogChangeCandidate(
            id=uuid.uuid4(),
            source_refs=[{"id": str(i.id), "type": "feedback"} for i in items],
            source_revision="sim-feedback-v1",
            sanitizer_version="san-1.0",
            model_id="simulated-v1",
            prompt_version="sim-p1",
            rule_version="sim-r1",
            proposal_hash=hashlib.sha256(proposal_json).hexdigest(),
            evidence={"cluster_id": str(cluster.id), "aggregation": "simulated"},
            proposal_json=PROPOSAL,
            status=CatalogCandidateStatus.CANDIDATE,
        )
        db.add(candidate)
        db.flush()
        print("candidate:", candidate.id, "hash", candidate.proposal_hash[:16])

        # 4) 候选生命周期：candidate -> frozen -> scored -> official
        for st in (
            CatalogCandidateStatus.FROZEN,
            CatalogCandidateStatus.SCORED,
            CatalogCandidateStatus.OFFICIAL,
        ):
            candidate.status = st
            db.flush()
            print("  candidate ->", st.value)

        # 5) 反馈项提升
        for i in items:
            i.status = FeedbackStatus.PROMOTED_TO_CATALOG_CHANGE
        db.commit()

        # 6) 商品修订审批（dispatch_command -> catalog_owner 审批 -> 上架 effect planned）
        proposer = db.execute(
            select(User).where(User.email == "proposer@x.com")
        ).scalar_one_or_none()
        if proposer is None:
            proposer = User(
                id=uuid.uuid4(),
                email="proposer@x.com",
                display_name="P6 Proposer",
                is_active=True,
            )
            db.add(proposer)
            db.flush()
            db.add(RoleAssignment(user_id=proposer.id, role=Role.COMMERCE_LEAD, scope="*"))
        approver = db.execute(
            select(User).where(User.email == "approver@x.com")
        ).scalar_one_or_none()
        if approver is None:
            approver = User(
                id=uuid.uuid4(),
                email="approver@x.com",
                display_name="P6 Approver",
                is_active=True,
            )
            db.add(approver)
            db.flush()
            db.add(RoleAssignment(user_id=approver.id, role=Role.CATALOG_OWNER, scope="*"))
        db.commit()

        result = dispatch_command(
            db,
            scope="catalog-revision",
            key=str(uuid.uuid4()),
            command_type="catalog-revision",
            payload={
                "sku": "SKU-YIFU-01",
                "title": PROPOSAL["title"],
                "category": PROPOSAL["category"],
                "description": PROPOSAL["description"],
                "proposed": PROPOSAL,
                "source_refs": [{"id": str(candidate.id), "type": "catalog_change_candidate"}],
                "source_revision": "sim-feedback-v1",
                "evidence": {"candidate_id": str(candidate.id)},
            },
            actor_user_id=proposer.id,
            correlation_id=str(uuid7()),
        )
        db.commit()
        run = db.get(WorkflowRun, uuid.UUID(result["workflowId"]))
        print("catalog-revision dispatched:", run.id, "status", run.status.value)

        # 审批（catalog_owner）
        from app.models.workflow import WorkItem, WorkItemStatus

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
        if item is not None:
            dec = submit_decision(
                db,
                work_item_id=item.id,
                user_id=approver.id,
                decision="approve",
                reason="simulated P6 approval",
                expected_workflow_version=run.version,
            )
            db.commit()
            print("catalog gate approved:", dec.get("status"))
        db.refresh(run)
        print("workflow status:", run.status.value)

        # 7) 查看 planned effect
        from app.models.effect import EffectLedgerEntry

        effs = (
            db.execute(select(EffectLedgerEntry).where(EffectLedgerEntry.approval_ref == run.id))
            .scalars()
            .all()
        )
        print("effects:", [(e.target_system, e.operation, e.status.value) for e in effs])
        print("DONE: feedback -> candidate -> catalog-revision -> approved")
    finally:
        db.close()


if __name__ == "__main__":
    main()
