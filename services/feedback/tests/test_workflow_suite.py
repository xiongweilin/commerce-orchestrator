import json

import pytest

from feedback_app.config import Settings
from feedback_app.workflow_suite import (
    WorkflowSuiteError,
    generate_cluster_narrative,
    generate_report_narrative,
    generate_sop_draft,
)


class FakeResponse:
    def __init__(self, output_name: str, payload: dict, status: str = "succeeded"):
        self.output_name = output_name
        self.payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": {
                "status": self.status,
                "error": "injected failure" if self.status != "succeeded" else None,
                "outputs": {self.output_name: json.dumps(self.payload, ensure_ascii=False)},
            }
        }


def settings() -> Settings:
    return Settings(
        dify_cluster_workflow_api_key="app-cluster",
        dify_sop_workflow_api_key="app-sop",
        dify_report_workflow_api_key="app-report",
    )


def test_cluster_narrative_rechecks_evidence_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "title": "提醒未送达",
        "observation": "两条工单指向提醒未送达。",
        "pending_cause": None,
        "evidence_ticket_ids": ["T2"],
        "explanation": "现象一致。",
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: FakeResponse("cluster_narrative_json", payload),
    )
    context = {"representative_tickets": [{"ticket_id": "T1", "quote": "未收到提醒"}]}
    with pytest.raises(WorkflowSuiteError, match="subset"):
        generate_cluster_narrative(settings(), "C1", context)


def test_sop_draft_parses_realistic_dify_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "title": "提醒问题处理流程",
        "applicable_when": "同类问题达到触发门槛时。",
        "steps": ["核查配置。", "记录结果并升级人工确认。"],
        "pending_cause": None,
        "evidence_ticket_ids": ["T1"],
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: FakeResponse("sop_draft_json", payload),
    )
    result = generate_sop_draft(settings(), "C1", {"evidence_ticket_ids": ["T1"]})
    assert result.steps == payload["steps"]


def test_report_rejects_cross_cluster_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "title": "客户反馈周报",
        "executive_summary": "本周反馈需要关注。",
        "observations": [
            {
                "cluster_id": "C1",
                "observation": "提醒问题需要关注。",
                "evidence_ticket_ids": ["T2"],
                "pending_cause": None,
                "recommended_action": "建议人工核查。",
            }
        ],
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: FakeResponse("report_narrative_json", payload),
    )
    context = {
        "clusters": [
            {"cluster_id": "C1", "evidence_ticket_ids": ["T1"]},
            {"cluster_id": "C2", "evidence_ticket_ids": ["T2"]},
        ]
    }
    with pytest.raises(WorkflowSuiteError, match="subset"):
        generate_report_narrative(settings(), "2026-W27", context)


def test_failed_dify_status_becomes_workflow_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: FakeResponse("sop_draft_json", {}, status="failed"),
    )
    with pytest.raises(WorkflowSuiteError, match="injected failure"):
        generate_sop_draft(settings(), "C1", {"evidence_ticket_ids": ["T1"]})
