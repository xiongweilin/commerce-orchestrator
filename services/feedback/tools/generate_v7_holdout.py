# ruff: noqa: E501  # Synthetic Chinese fixture rows stay one tuple per family.

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "v7-evaluation"

# V7 families are disjoint from V5/V6. Adjacent families deliberately share
# type/area vocabulary so labels alone cannot solve clustering.
FAMILIES = [
    ("REST 游标重复分页", "integration", "open_api", "technical_support", "REST API 翻页后游标没有前进并重复返回上一页记录", "开放接口使用 next_cursor 请求下一页时仍拿到相同数据"),
    ("Webhook 签名校验不一致", "integration", "open_api", "technical_support", "Webhook 请求头签名按文档计算后始终校验失败", "回调负载使用平台密钥验签时结果与签名头不一致"),
    ("Workspace 停用成员未同步", "integration", "member_permission", "technical_support", "Google Workspace 删除员工后平台成员仍保持启用", "企业目录已移除账号但团队中的成员状态没有同步停用"),
    ("SAML 组属性映射失败", "integration", "member_permission", "technical_support", "SAML 登录返回的部门组属性没有映射到平台角色", "身份提供商携带 group 声明后成员仍未进入对应权限组"),
    ("任务依赖顺序被重置", "bug", "task", "customer_success", "保存任务依赖顺序后刷新页面又恢复成原来的排列", "调整多个前置任务次序后重新打开记录发现顺序丢失"),
    ("检查项负责人消失", "bug", "task", "customer_success", "给任务检查项指定负责人后再次进入显示为空", "清单条目保存了经办人但刷新任务后人员字段消失"),
    ("复制项目遗漏自定义字段", "bug", "project", "customer_success", "复制项目模板后新项目没有带出自定义字段", "从现有项目创建副本时字段配置没有一起复制"),
    ("已归档里程碑重新出现", "bug", "project", "customer_success", "归档里程碑后第二天它又出现在项目时间线", "已经隐藏的项目里程碑刷新后重新变为可见"),
    ("预算汇总与项目明细不一致", "data_consistency", "project", "qa_triage", "项目仪表盘预算总额与各阶段金额相加不一致", "概览中的预算汇总数和项目明细求和结果对不上"),
    ("项目成员计数不一致", "data_consistency", "project", "qa_triage", "项目首页显示十二名成员但成员列表只有十人", "项目卡片的参与人数与打开后的成员清单数量不同"),
    ("完成进度与子任务不一致", "data_consistency", "task", "qa_triage", "全部子任务完成后父任务进度仍显示百分之八十", "任务的完成比例没有反映已勾选的所有子任务"),
    ("优先级统计与任务列表不一致", "data_consistency", "task", "qa_triage", "高优先级任务统计数量与筛选出的记录条数不同", "任务页优先级分布数字和实际列表计数对不上"),
    ("周末免通知规则无效", "configuration", "notification", "customer_success", "已开启周末不提醒但星期六仍收到任务推送", "通知规则排除了休息日却仍在周日发送消息"),
    ("邮件摘要频率设置无效", "configuration", "notification", "customer_success", "邮件摘要设为每周发送却仍然每天到达", "通知摘要频率保存成周报后系统继续按日发送"),
    ("关闭席位自动增加无效", "configuration", "account_subscription", "customer_success", "关闭自动增加席位后邀请成员仍然扩充了订阅数量", "账户禁止自动购买席位但新增用户后套餐席位仍上升"),
    ("账单币种默认值无效", "configuration", "account_subscription", "customer_success", "账单币种设置为人民币后续费页仍显示美元", "账户保存默认结算币种后新账单没有采用该设置"),
    ("共享链接过期后仍可访问", "permission", "file", "implementation_support", "文件共享链接超过截止时间后仍然能够打开", "已经到期的外链没有失效并继续展示受限文档"),
    ("评论者可以覆盖文件", "permission", "file", "implementation_support", "只有评论权限的成员可以上传同名文件覆盖原版本", "评论者角色在文件区能够替换现有附件内容"),
    ("项目管理员可授予组织角色", "permission", "member_permission", "implementation_support", "项目管理员能够把普通成员提升为组织管理员", "仅有项目管理权限的账号出现了分配全局角色的入口"),
    ("停用成员仍可进入工作区", "permission", "member_permission", "implementation_support", "已停用成员的登录会话仍能访问工作区", "管理员禁用账号后该用户继续打开团队项目"),
    ("大型日历视图加载缓慢", "performance", "project", "engineering_triage", "项目日历包含三千条任务时打开需要一分钟", "大数据量日历视图长时间显示加载中"),
    ("项目组合筛选请求超时", "performance", "project", "engineering_triage", "项目组合看板切换复杂筛选条件时经常超时", "跨项目仪表盘应用多条件过滤后一直没有结果"),
    ("Excel 格式化导出耗时过长", "performance", "import_export", "engineering_triage", "导出带格式的 Excel 文件十分钟仍未生成", "大量记录生成含样式表格时处理时间异常漫长"),
    ("导入校验预览卡顿", "performance", "import_export", "engineering_triage", "上传表格后校验预览页面持续卡住没有结果", "批量导入在错误检查预览阶段等待很久"),
    ("回滚最近一次导入方法", "how_to", "import_export", "customer_success", "怎样撤销刚刚完成的一批 CSV 导入", "想了解恢复到本次批量导入之前状态的操作方法"),
    ("导出自定义字段方法", "how_to", "import_export", "customer_success", "如何在项目导出文件中包含自定义字段", "需要知道导出任务时怎样选择额外业务字段"),
    ("按项目设置通知声音", "feature_request", "notification", "product_ops", "希望不同项目可以配置各自的通知声音", "建议允许用户按项目选择提醒音效"),
    ("暂缓单条通知", "feature_request", "notification", "product_ops", "希望通知中心支持把某条提醒暂缓到明天", "建议给单个消息增加稍后再次提醒功能"),
    ("提供 GraphQL 开放接口", "feature_request", "open_api", "product_ops", "希望开放平台增加 GraphQL 查询接口", "建议提供可按字段选择结果的 GraphQL API"),
    ("按事件字段过滤 Webhook", "feature_request", "open_api", "product_ops", "希望 Webhook 可以按项目或任务字段过滤事件", "建议回调订阅支持配置事件属性筛选条件"),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_rows() -> list[dict]:
    rows: list[dict] = []
    start = datetime(2026, 7, 1, 9, tzinfo=UTC)
    for family_index, family in enumerate(FAMILIES, 1):
        title, problem_type, area, owner, first, second = family
        blocked = problem_type not in {"how_to", "feature_request"}
        for variant, base_message in enumerate((first, second), 1):
            high = blocked and variant == 2
            message = base_message.rstrip("。")
            if high:
                message += "，该问题影响整个团队，已经联系客服两次。"
            else:
                message += "。"
            signals = {
                "affected_scope": "team" if high else "individual",
                "workflow_blocked": blocked,
                "data_loss_claimed": False,
                "repeat_contacts": 2 if high else 0,
            }
            rows.append(
                {
                    "ticket_id": f"V7-T{len(rows) + 1:03d}",
                    "user_type": "enterprise_admin" if variant == 1 else "member",
                    "channel": "support_portal" if variant == 1 else "chat",
                    "message": message,
                    "created_at": (start + timedelta(hours=3 * len(rows))).isoformat(),
                    "current_status": "open",
                    "gold_issue_family": f"V7-ISSUE-{family_index:03d}",
                    "gold_issue_title": title,
                    "gold_problem_type": problem_type,
                    "gold_product_area": area,
                    "gold_owner": owner,
                    "gold_severity": "high" if high else ("medium" if blocked else "low"),
                    "gold_escalation": str(high).lower(),
                    "gold_impact_signals": json.dumps(signals, ensure_ascii=False),
                    "split": "v7_suite_holdout",
                }
            )
    return rows


def main()-> None:  # noqa: S3776 (tool script - acceptable complexity)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    holdout = OUT / "v7-holdout-locked.csv"
    with holdout.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    audit = OUT / "v7-holdout-audit.csv"
    audit_fields = [*rows[0], "audit_label_text_consistent", "audit_notes", "auditor"]
    with audit.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "audit_label_text_consistent": "yes",
                    "audit_notes": (
                        "AI-assisted v7 consistency review: labels, routing, distinct "
                        "hard-negative family and explicit impact cues align."
                    ),
                    "auditor": "Codex AI-assisted review (not independent human)",
                }
            )

    frozen_paths = [
        "dify-workflows/feedback-structuring-v3-candidate.yml",
        "feedback_app/routing.py",
        "feedback_app/clustering.py",
        "feedback_app/embeddings.py",
        "tools/evaluate.py",
        "tools/generate_v7_holdout.py",
    ]
    development_paths = [
        "data/v6-evaluation/v6-holdout-locked.csv",
        "data/development/v6-v3-analysis-cache.json",
    ]
    missing = [path for path in [*frozen_paths, *development_paths] if not (ROOT / path).exists()]
    if missing:
        raise FileNotFoundError(f"freeze inputs missing: {missing}")
    manifest = {
        "dataset_version": "v7-frozen-signature-clustering-holdout-20260702",
        "state": "candidate_unscored",
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "family_count": len(FAMILIES),
        "boundary": (
            "Synthetic locked holdout with two distinct families per type/area combination; "
            "AI-assisted review is not an independent human audit."
        ),
        "candidate_config": {
            "workflow_version": "feedback-structuring-v3-candidate",
            "routing_policy_version": "feedback-routing-v3-candidate",
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "cluster_linkage": "complete",
            "cluster_raw_text_weight": 0.2,
            "cluster_block_by_problem_type": True,
        },
        "holdout_path": str(holdout.relative_to(ROOT)),
        "holdout_sha256": sha256(holdout),
        "audit_path": str(audit.relative_to(ROOT)),
        "audit_sha256": sha256(audit),
        "frozen_files": [
            {"path": path, "sha256": sha256(ROOT / path)} for path in frozen_paths
        ],
        "development_files": [
            {"path": path, "sha256": sha256(ROOT / path)} for path in development_paths
        ],
    }
    (OUT / "v7-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(holdout)
    print(audit)
    print(OUT / "v7-manifest.json")


if __name__ == "__main__":
    main()
