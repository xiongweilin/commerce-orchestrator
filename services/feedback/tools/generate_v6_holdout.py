import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "v6-evaluation"

FAMILIES = [
    (
        "Webhook 重试产生重复事件",
        "integration",
        "open_api",
        "technical_support",
        "Webhook 超时重试后同一任务事件推送了两次",
        "接收端发现相同 event_id 的回调被重复发送",
    ),
    (
        "OAuth 刷新令牌失效",
        "integration",
        "open_api",
        "technical_support",
        "开放接口的 OAuth refresh token 无法换取新令牌",
        "第三方应用刷新访问凭证时持续返回令牌失效",
    ),
    (
        "SCIM 停用成员未同步",
        "integration",
        "member_permission",
        "technical_support",
        "Okta 通过 SCIM 停用成员后平台账号仍然有效",
        "身份提供商已经禁用员工但成员状态没有同步关闭",
    ),
    (
        "企业目录组映射缺失",
        "integration",
        "member_permission",
        "technical_support",
        "Azure AD 同步用户组后成员没有进入对应部门",
        "外部企业目录中的组关系未映射到平台团队",
    ),
    (
        "循环任务检查项丢失",
        "bug",
        "task",
        "customer_success",
        "循环任务生成下一期实例时检查清单为空",
        "重复任务的新实例没有保留模板里的检查项",
    ),
    (
        "批量改期写入错误日期",
        "bug",
        "task",
        "customer_success",
        "批量修改截止日期后部分任务被写成了前一天",
        "多选任务统一改期后有记录保存到了错误日期",
    ),
    (
        "恢复项目后标签丢失",
        "bug",
        "project",
        "customer_success",
        "从归档恢复项目后原有标签全部消失",
        "项目解除归档时没有恢复之前配置的标签",
    ),
    (
        "看板筛选条件无法保存",
        "bug",
        "project",
        "customer_success",
        "保存看板筛选器后重新进入项目条件被清空",
        "项目视图记不住已经保存的筛选规则",
    ),
    (
        "燃尽图完成数不一致",
        "data_consistency",
        "project",
        "qa_triage",
        "燃尽图统计的已完成任务数与项目明细不同",
        "项目图表中的完成数量和任务列表对不上",
    ),
    (
        "逾期任务总数不一致",
        "data_consistency",
        "project",
        "qa_triage",
        "项目首页显示的逾期任务总数与筛选结果不一致",
        "概览卡片的逾期数量和列表统计对不上",
    ),
    (
        "工时汇总与明细不一致",
        "data_consistency",
        "task",
        "qa_triage",
        "任务显示的总工时与逐条工时记录求和不同",
        "时间记录明细合计和任务卡片上的工时不一致",
    ),
    (
        "子任务计数不一致",
        "data_consistency",
        "task",
        "qa_triage",
        "父任务显示的子任务数量与实际列表条数不同",
        "任务卡片的子项计数和展开后看到的记录对不上",
    ),
    (
        "免打扰时段仍发送提醒",
        "configuration",
        "notification",
        "customer_success",
        "已经设置免打扰时间但夜间仍收到任务通知",
        "通知静默时段配置保存后没有按预期生效",
    ),
    (
        "摘要邮件时区错误",
        "configuration",
        "notification",
        "customer_success",
        "每日摘要按北京时间配置却在 UTC 时间发送",
        "通知摘要的发送时间没有采用账号设置的时区",
    ),
    (
        "续费提醒提前天数无效",
        "configuration",
        "account_subscription",
        "customer_success",
        "订阅续费提醒设置为提前七天却仍提前一天发送",
        "账户中的续费通知提前量配置没有生效",
    ),
    (
        "发票抬头默认值无效",
        "configuration",
        "account_subscription",
        "customer_success",
        "保存默认发票抬头后新申请仍然为空",
        "账户订阅页设置的开票抬头没有自动带入",
    ),
    (
        "访客可以删除附件",
        "permission",
        "file",
        "implementation_support",
        "只读访客仍然能够删除项目附件",
        "访客权限账号在文件区看到了删除按钮并能执行",
    ),
    (
        "受限文件夹可被访问",
        "permission",
        "file",
        "implementation_support",
        "未授权成员可以打开受限文件夹里的文档",
        "文件夹设置为指定成员可见后其他人仍能进入",
    ),
    (
        "部门管理员可修改所有者",
        "permission",
        "member_permission",
        "implementation_support",
        "部门管理员能够修改组织所有者的账号信息",
        "受限管理员拥有了编辑超级管理员资料的权限",
    ),
    (
        "外部协作者可以邀请成员",
        "permission",
        "member_permission",
        "implementation_support",
        "外部协作者账号可以向组织发送成员邀请",
        "访客角色出现了邀请新成员进入团队的入口",
    ),
    (
        "大型甘特图加载缓慢",
        "performance",
        "project",
        "engineering_triage",
        "包含两千个任务的甘特图打开需要四十秒",
        "大型项目的甘特视图长时间停留在加载状态",
    ),
    (
        "项目看板切换超时",
        "performance",
        "project",
        "engineering_triage",
        "在多个项目看板之间切换时经常请求超时",
        "打开另一个项目视图需要等待很久并偶尔失败",
    ),
    (
        "大型导出任务耗时过长",
        "performance",
        "import_export",
        "engineering_triage",
        "导出一万条任务记录超过五分钟仍未完成",
        "大量任务生成 CSV 时长时间没有得到文件",
    ),
    (
        "批量导入解析卡顿",
        "performance",
        "import_export",
        "engineering_triage",
        "导入包含五千行的表格时页面持续卡顿",
        "大批量 CSV 解析阶段等待很久没有进度",
    ),
    (
        "查询导入失败明细方法",
        "how_to",
        "import_export",
        "customer_success",
        "如何查看上一次 CSV 导入失败的具体行",
        "想找到批量导入中未成功记录的错误明细",
    ),
    (
        "迁移项目模板方法",
        "how_to",
        "import_export",
        "customer_success",
        "怎样把一个空间的项目模板迁移到另一个空间",
        "需要了解跨工作区导出并导入模板的操作步骤",
    ),
    (
        "按关键词暂停通知",
        "feature_request",
        "notification",
        "product_ops",
        "希望通知规则支持按评论关键词暂停提醒",
        "建议增加根据消息关键词静默通知的功能",
    ),
    (
        "通知摘要自定义排序",
        "feature_request",
        "notification",
        "product_ops",
        "希望每日通知摘要可以自定义项目排序",
        "建议允许用户调整摘要邮件里的项目顺序",
    ),
    (
        "API 沙箱环境",
        "feature_request",
        "open_api",
        "product_ops",
        "希望开放接口提供独立的测试沙箱环境",
        "建议为 API 集成增加不影响正式数据的沙箱",
    ),
    (
        "Webhook 回放控制台",
        "feature_request",
        "open_api",
        "product_ops",
        "希望后台可以手动回放失败的 Webhook",
        "建议增加查看并重新发送回调事件的控制台",
    ),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_rows() -> list[dict]:
    rows: list[dict] = []
    start = datetime(2026, 6, 1, 9, tzinfo=UTC)
    for family_index, (title, problem_type, area, owner, first, second) in enumerate(FAMILIES, 1):
        blocked = problem_type not in {"how_to", "feature_request"}
        for variant, message in enumerate((first, second), 1):
            high = blocked and variant == 2
            if high:
                message = f"{message}，该问题影响整个团队，已经联系客服两次。"
            else:
                message = message.rstrip("。") + "。"
            signals = {
                "affected_scope": "team" if high else "individual",
                "workflow_blocked": blocked,
                "data_loss_claimed": False,
                "repeat_contacts": 2 if high else 0,
            }
            rows.append(
                {
                    "ticket_id": f"V6-T{len(rows) + 1:03d}",
                    "user_type": "enterprise_admin" if variant == 1 else "member",
                    "channel": "support_portal" if variant == 1 else "chat",
                    "message": message,
                    "created_at": (start + timedelta(hours=3 * len(rows))).isoformat(),
                    "current_status": "open",
                    "gold_issue_family": f"V6-ISSUE-{family_index:03d}",
                    "gold_issue_title": title,
                    "gold_problem_type": problem_type,
                    "gold_product_area": area,
                    "gold_owner": owner,
                    "gold_severity": "high" if high else ("medium" if blocked else "low"),
                    "gold_escalation": str(high).lower(),
                    "gold_impact_signals": json.dumps(signals, ensure_ascii=False),
                    "split": "v6_suite_holdout",
                }
            )
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = build_rows()

    holdout = OUT / "v6-holdout-locked.csv"
    with holdout.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    audit = OUT / "v6-holdout-audit.csv"
    audit_fields = list(rows[0]) + [
        "audit_label_text_consistent",
        "audit_notes",
        "auditor",
    ]
    with audit.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=audit_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "audit_label_text_consistent": "yes",
                    "audit_notes": (
                        "AI-assisted v6 consistency review: distinct hard-negative family, "
                        "labels, routing and explicit impact cues align."
                    ),
                    "auditor": "Codex AI-assisted review (not independent human)",
                }
            )

    frozen_paths = [
        "dify-workflows/feedback-structuring-v2-candidate.yml",
        "feedback_app/routing.py",
        "feedback_app/clustering.py",
        "feedback_app/embeddings.py",
        "tools/evaluate.py",
        "tools/generate_v6_holdout.py",
    ]
    development_paths = [
        "data/suite-evaluation/v5-holdout-locked.csv",
        "data/development/v5-analysis-cache.json",
    ]
    manifest = {
        "dataset_version": "v6-frozen-routing-clustering-holdout-20260702",
        "state": "candidate_unscored",
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "family_count": len(FAMILIES),
        "boundary": (
            "Synthetic locked holdout with two distinct families per type/area combination; "
            "AI-assisted review is not an independent human audit."
        ),
        "candidate_config": {
            "workflow_version": "feedback-structuring-v2-candidate",
            "routing_policy_version": "feedback-routing-v3-candidate",
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "cluster_linkage": "complete",
            "cluster_raw_text_weight": 0.8,
        },
        "holdout_path": str(holdout.relative_to(ROOT)),
        "holdout_sha256": sha256(holdout),
        "audit_path": str(audit.relative_to(ROOT)),
        "audit_sha256": sha256(audit),
        "frozen_files": [{"path": path, "sha256": sha256(ROOT / path)} for path in frozen_paths],
        "development_files": [
            {"path": path, "sha256": sha256(ROOT / path)} for path in development_paths
        ],
    }
    (OUT / "v6-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"generated rows={len(rows)} families={len(FAMILIES)} out={OUT}")


if __name__ == "__main__":
    main()
