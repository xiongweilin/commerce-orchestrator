from feedback_app.routing import (
    arbitrate_classification,
    derive_severity,
    needs_escalation,
    route_owner,
)
from feedback_app.schemas import (
    AffectedScope,
    ImpactSignals,
    Owner,
    ProblemType,
    ProductArea,
    Severity,
)


def test_owner_is_deterministic_not_model_controlled() -> None:
    assert route_owner(ProblemType.INTEGRATION, ProductArea.OPEN_API) == Owner.TECHNICAL_SUPPORT
    assert route_owner(ProblemType.DATA_CONSISTENCY, ProductArea.IMPORT_EXPORT) == Owner.QA_TRIAGE
    assert route_owner(ProblemType.FEATURE_REQUEST, ProductArea.TASK) == Owner.PRODUCT_OPS


def test_organization_wide_block_is_critical_and_escalated() -> None:
    signals = ImpactSignals(
        affected_scope=AffectedScope.ORGANIZATION,
        workflow_blocked=True,
        repeat_contacts=0,
    )
    severity = derive_severity(signals)
    assert severity == Severity.CRITICAL
    assert needs_escalation(severity, signals)


def test_sentiment_is_not_part_of_severity_contract() -> None:
    signals = ImpactSignals(affected_scope=AffectedScope.INDIVIDUAL, workflow_blocked=False)
    assert derive_severity(signals) == Severity.LOW


def test_official_routing_policy_does_not_promote_candidate_rules() -> None:
    result = arbitrate_classification(
        "从 Jira 同步任务后负责人字段一直为空。",
        ProblemType.DATA_CONSISTENCY,
        ProductArea.OPEN_API,
        "feedback-routing-v1",
    )
    assert result == (ProblemType.DATA_CONSISTENCY, ProductArea.OPEN_API, "llm")


def test_candidate_routing_recovers_explicit_jira_member_integration() -> None:
    problem_type, product_area, source = arbitrate_classification(
        "从 Jira 同步任务后负责人字段一直为空。",
        ProblemType.DATA_CONSISTENCY,
        ProductArea.OPEN_API,
        "feedback-routing-v3-candidate",
    )
    assert problem_type == ProblemType.INTEGRATION
    assert product_area == ProductArea.MEMBER_PERMISSION
    assert source.startswith("deterministic_override:")


def test_candidate_routing_separates_internal_bug_from_count_mismatch() -> None:
    bug, _, _ = arbitrate_classification(
        "点击下载最新附件却拿到了上一个历史版本。",
        ProblemType.DATA_CONSISTENCY,
        ProductArea.FILE,
        "feedback-routing-v3-candidate",
    )
    consistency, _, _ = arbitrate_classification(
        "任务卡片显示的评论数量与详情页实际条数不同。",
        ProblemType.BUG,
        ProductArea.TASK,
        "feedback-routing-v3-candidate",
    )
    assert bug == ProblemType.BUG
    assert consistency == ProblemType.DATA_CONSISTENCY


def test_candidate_file_object_overrides_permission_surface() -> None:
    _, product_area, _ = arbitrate_classification(
        "只读访客账号仍然可以替换项目附件。",
        ProblemType.PERMISSION,
        ProductArea.MEMBER_PERMISSION,
        "feedback-routing-v3-candidate",
    )
    assert product_area == ProductArea.FILE
