from datetime import UTC, datetime, timedelta

from feedback_app.reports import build_weekly_report, report_to_markdown, trend_for_dates
from feedback_app.sop import build_sop_candidate


def test_report_separates_observation_from_pending_cause() -> None:
    now = datetime.now(UTC)
    tickets = [{"created_at": now - timedelta(days=1), "severity": "medium"}]
    clusters = [
        {
            "title": "导入后字段映射异常",
            "member_count": 12,
            "representative_ticket_ids": ["T021", "T044", "T087"],
            "root_cause_hypothesis": "可能与字段映射提示不足有关",
            "recommended_action": "产品运营确认导入页提示",
        }
    ]
    report = build_weekly_report(tickets, clusters, now)
    markdown = report_to_markdown(report)
    assert "待确认原因" in markdown
    assert "证据：T021、T044、T087" in markdown
    assert "原因是字段映射规则" not in markdown


def test_sop_is_candidate_only_and_requires_trigger() -> None:
    cluster = {
        "title": "通知未送达",
        "member_count": 6,
        "trend": "rising",
        "severity": "medium",
        "representative_ticket_ids": ["T001"],
    }
    candidate = build_sop_candidate(cluster)
    assert candidate is not None
    assert candidate["status"] == "pending_review"
    assert "不得自动写入正式 SOP" in candidate["prohibited_actions"]


def test_trend_requires_minimum_volume() -> None:
    now = datetime.now(UTC)
    assert trend_for_dates([now - timedelta(days=1)] * 4, now) == "stable"

