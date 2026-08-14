from .schemas import ImpactSignals, Owner, ProblemType, ProductArea, Severity

INTEGRATION_CUES = (
    "Jira",
    "外部日历",
    "企业云盘",
    "外部网盘",
    "开放接口",
    "API",
    "Webhook",
    "SSO",
    "SCIM",
    "cursor",
)
DATA_COMPARISON_CUES = ("统计", "数量", "计数", "比例", "席位", "对不上", "不一致")
INTERNAL_BUG_CUES = (
    "恢复原来",
    "没有保留",
    "缺少",
    "旧版本",
    "历史版本",
    "另一个任务",
    "不是消息里",
    "丢失",
)


def arbitrate_classification(
    message: str,
    problem_type: ProblemType,
    product_area: ProductArea,
    policy_version: str,
) -> tuple[ProblemType, ProductArea, str]:
    """Apply narrow candidate rules; official v1 remains model-led."""
    if policy_version != "feedback-routing-v3-candidate":
        return problem_type, product_area, "llm"

    final_type = problem_type
    final_area = product_area
    reasons: list[str] = []

    if any(cue in message for cue in INTEGRATION_CUES):
        final_type = ProblemType.INTEGRATION
        reasons.append("explicit_external_integration")
    elif any(cue in message for cue in DATA_COMPARISON_CUES):
        final_type = ProblemType.DATA_CONSISTENCY
        reasons.append("explicit_data_comparison")
    elif problem_type == ProblemType.DATA_CONSISTENCY and any(
        cue in message for cue in INTERNAL_BUG_CUES
    ):
        final_type = ProblemType.BUG
        reasons.append("internal_state_or_target_failure")

    if "Jira" in message and any(cue in message for cue in ("负责人", "经办人", "成员")):
        final_area = ProductArea.MEMBER_PERMISSION
        reasons.append("jira_member_identity")
    elif any(cue in message for cue in ("附件", "网盘", "文件版本", "文件名")):
        final_area = ProductArea.FILE
        reasons.append("file_object")

    source = "deterministic_override:" + ",".join(reasons) if reasons else "llm"
    return final_type, final_area, source


def supplement_impact_signals(message: str, model_signals: ImpactSignals) -> ImpactSignals:
    """Add only explicit, high-precision text cues missed by the model."""
    scope = model_signals.affected_scope
    if any(cue in message for cue in ("全公司", "整个组织", "所有部门")):
        scope = "organization"
    elif any(cue in message for cue in ("整个团队", "整个项目组", "全体成员")):
        scope = "team"

    repeat_contacts = model_signals.repeat_contacts
    if any(cue in message for cue in ("联系客服两次", "联系了两次", "多次联系客服")):
        repeat_contacts = max(repeat_contacts, 2)

    workflow_blocked = model_signals.workflow_blocked or any(
        cue in message for cue in ("无法", "失败", "找不到", "空白", "超时", "没有回执")
    )
    data_loss_claimed = model_signals.data_loss_claimed or any(
        cue in message for cue in ("数据丢失", "记录丢失", "内容丢失")
    )
    return ImpactSignals(
        affected_scope=scope,
        workflow_blocked=workflow_blocked,
        data_loss_claimed=data_loss_claimed,
        repeat_contacts=repeat_contacts,
    )


def route_owner(problem_type: ProblemType, product_area: ProductArea) -> Owner:
    if problem_type == ProblemType.PERFORMANCE:
        return Owner.ENGINEERING_TRIAGE
    if problem_type == ProblemType.DATA_CONSISTENCY:
        return Owner.QA_TRIAGE
    if problem_type == ProblemType.FEATURE_REQUEST:
        return Owner.PRODUCT_OPS
    if problem_type == ProblemType.INTEGRATION or product_area == ProductArea.OPEN_API:
        return Owner.TECHNICAL_SUPPORT
    if problem_type == ProblemType.PERMISSION or product_area == ProductArea.MEMBER_PERMISSION:
        return Owner.IMPLEMENTATION_SUPPORT
    return Owner.CUSTOMER_SUCCESS


def derive_severity(signals: ImpactSignals) -> Severity:
    if signals.data_loss_claimed:
        return Severity.CRITICAL
    if signals.affected_scope.value == "organization" and signals.workflow_blocked:
        return Severity.CRITICAL
    if signals.workflow_blocked and signals.affected_scope.value in {"team", "organization"}:
        return Severity.HIGH
    if signals.repeat_contacts >= 2:
        return Severity.HIGH
    if signals.workflow_blocked:
        return Severity.MEDIUM
    return Severity.LOW


def needs_escalation(severity: Severity, signals: ImpactSignals) -> bool:
    return severity in {Severity.HIGH, Severity.CRITICAL} or signals.repeat_contacts >= 2
