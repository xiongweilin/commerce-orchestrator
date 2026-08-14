def build_sop_candidate(cluster: dict) -> dict | None:
    if cluster["member_count"] < 5:
        return None
    if cluster.get("trend") != "rising" and cluster.get("severity") not in {"high", "critical"}:
        return None
    evidence = cluster.get("representative_ticket_ids", [])
    return {
        "title": f"{cluster['title']}处理流程（候选）",
        "applicable_when": f"同类问题达到 {cluster['member_count']} 条，需人工确认适用范围",
        "steps": [
            "核对账号、项目和功能配置",
            "按代表工单证据复现问题",
            "无法确认时升级给建议责任方",
        ],
        "suggested_reply": "我们已记录该问题，正在结合配置和日志进一步核查。",
        "escalation_condition": "问题阻塞团队协作、涉及数据丢失声明或重复联系两次以上",
        "prohibited_actions": ["不得把待确认原因表述为已证实根因", "不得自动写入正式 SOP"],
        "evidence_ticket_ids": evidence,
        "status": "pending_review",
    }

