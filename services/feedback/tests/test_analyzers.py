import json
from datetime import UTC, datetime

import httpx
import pytest

from feedback_app.analyzers import AnalyzerError, DemoAnalyzer, DifyAnalyzer
from feedback_app.config import Settings
from feedback_app.schemas import TicketInput


class FakeResponse:
    def __init__(self, payload: dict | None = None, exc: Exception | None = None):
        self.payload = payload or {}
        self.exc = exc

    def raise_for_status(self) -> None:
        if self.exc:
            raise self.exc

    def json(self) -> dict:
        return self.payload


def ticket(message: str = "API integration is blocked") -> TicketInput:
    return TicketInput(ticket_id="T-ANALYZER", message=message, created_at=datetime.now(UTC))


def valid_analysis_payload() -> dict:
    return {
        "summary": "API integration is blocked",
        "problem_type": "integration",
        "product_area": "open_api",
        "sentiment": "frustrated",
        "llm_owner_suggestion": "technical_support",
        "root_cause_hypothesis": "Webhook signature mismatch",
        "evidence_spans": [{"quote": "API", "start": 0, "end": 3}],
        "impact_signals": {
            "affected_scope": "team",
            "workflow_blocked": True,
            "data_loss_claimed": False,
            "repeat_contacts": 0,
        },
    }


def settings_with_key() -> Settings:
    return Settings(dify_feedback_workflow_api_key="feedback-key", dify_base_url="http://dify")


def test_dify_analyzer_requires_api_key() -> None:
    with pytest.raises(AnalyzerError, match="not configured"):
        DifyAnalyzer(Settings(dify_feedback_workflow_api_key="")).analyze(ticket())


def test_dify_analyzer_accepts_dict_output(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": {"outputs": {"analysis_json": valid_analysis_payload()}}}
    monkeypatch.setattr("feedback_app.analyzers.httpx.post", lambda *a, **kw: FakeResponse(payload))

    result = DifyAnalyzer(settings_with_key()).analyze(ticket())

    assert result.problem_type == "integration"


def test_dify_analyzer_accepts_json_string_output(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps(valid_analysis_payload(), ensure_ascii=False)
    payload = {"data": {"outputs": {"result": raw}}}
    monkeypatch.setattr("feedback_app.analyzers.httpx.post", lambda *a, **kw: FakeResponse(payload))

    result = DifyAnalyzer(settings_with_key()).analyze(ticket())

    assert result.product_area == "open_api"


def test_dify_analyzer_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": {"outputs": {"analysis_json": "{"}}}
    monkeypatch.setattr("feedback_app.analyzers.httpx.post", lambda *a, **kw: FakeResponse(payload))

    with pytest.raises(AnalyzerError, match="invalid JSON"):
        DifyAnalyzer(settings_with_key()).analyze(ticket())


def test_dify_analyzer_rejects_missing_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"data": {"outputs": {}}}
    monkeypatch.setattr("feedback_app.analyzers.httpx.post", lambda *a, **kw: FakeResponse(payload))

    with pytest.raises(AnalyzerError, match="missing analysis_json"):
        DifyAnalyzer(settings_with_key()).analyze(ticket())


def test_dify_analyzer_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "feedback_app.analyzers.httpx.post",
        lambda *a, **kw: FakeResponse(exc=httpx.HTTPError("provider unavailable")),
    )

    with pytest.raises(httpx.HTTPError, match="provider unavailable"):
        DifyAnalyzer(settings_with_key()).analyze(ticket())


def test_demo_analyzer_detects_organization_scope() -> None:
    message = "\u5168\u516c\u53f8 members cannot receive notifications"
    result = DemoAnalyzer().analyze(ticket(message))

    assert result.impact_signals.affected_scope == "organization"
