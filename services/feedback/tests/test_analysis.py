from feedback_app.analysis import finalize_analysis
from feedback_app.schemas import LLMAnalysis, Owner, ReviewStatus


def test_final_analysis_routes_owner_and_locates_quote() -> None:
    message = "我们整个项目组都收不到到期提醒，已经联系两次。"
    analysis = LLMAnalysis.model_validate(
        {
            "summary": "团队无法收到任务到期通知",
            "issue_signature": "任务到期通知未送达",
            "problem_type": "configuration",
            "product_area": "notification",
            "sentiment": "frustrated",
            "llm_owner_suggestion": "engineering_triage",
            "root_cause_hypothesis": "可能未启用提醒",
            "impact_signals": {
                "affected_scope": "team",
                "workflow_blocked": False,
                "data_loss_claimed": False,
                "repeat_contacts": 2,
            },
            "evidence_spans": [{"quote": "我们整个项目组都收不到到期提醒"}],
        }
    )
    final = finalize_analysis(message, analysis, "v1", "test")
    assert final.suggested_owner == Owner.CUSTOMER_SUCCESS
    assert final.llm_owner_suggestion == Owner.ENGINEERING_TRIAGE
    assert final.review_status == ReviewStatus.ACCEPTED
    assert final.needs_escalation
    assert final.issue_signature == "任务到期通知未送达"


def test_unverifiable_evidence_forces_review() -> None:
    analysis = LLMAnalysis.model_validate(
        {
            "summary": "通知异常",
            "problem_type": "bug",
            "product_area": "notification",
            "sentiment": "confused",
            "impact_signals": {},
            "evidence_spans": [{"quote": "原文没有这句话"}],
        }
    )
    final = finalize_analysis("我的通知不太对", analysis, "v1", "test")
    assert final.issue_signature == "通知异常"
    assert final.review_status == ReviewStatus.NEEDS_REVIEW
    assert "no_located_evidence" in final.review_reasons


def test_explicit_text_cues_supplement_incomplete_model_impact_signals() -> None:
    message = "整个团队无法导入文件，已经联系客服两次。"
    analysis = LLMAnalysis.model_validate(
        {
            "summary": "团队导入失败",
            "problem_type": "data_consistency",
            "product_area": "import_export",
            "sentiment": "frustrated",
            "impact_signals": {},
            "evidence_spans": [{"quote": "整个团队无法导入文件"}],
        }
    )
    final = finalize_analysis(message, analysis, "v1", "test")
    assert final.impact_signals.affected_scope.value == "team"
    assert final.impact_signals.workflow_blocked
    assert final.impact_signals.repeat_contacts == 2
    assert final.needs_escalation
