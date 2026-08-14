import json

import httpx

from .config import Settings
from .schemas import LLMAnalysis, TicketInput


class AnalyzerError(RuntimeError):
    pass


class DifyAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def analyze(self, ticket: TicketInput) -> LLMAnalysis:
        if not self.settings.dify_feedback_workflow_api_key:
            raise AnalyzerError("DIFY_FEEDBACK_WORKFLOW_API_KEY is not configured")
        response = httpx.post(
            f"{self.settings.dify_base_url.rstrip('/')}/workflows/run",
            headers={
                "Authorization": f"Bearer {self.settings.dify_feedback_workflow_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": {
                    "ticket_id": ticket.ticket_id,
                    "user_type": ticket.user_type,
                    "channel": ticket.channel,
                    "message": ticket.message,
                    "created_at": ticket.created_at.isoformat(),
                },
                "response_mode": "blocking",
                "user": "feedback-analysis-worker",
            },
            timeout=self.settings.dify_timeout_seconds,
        )
        response.raise_for_status()
        outputs = response.json().get("data", {}).get("outputs", {})
        raw = outputs.get("analysis_json") or outputs.get("result")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AnalyzerError("Dify returned invalid JSON") from exc
        if not isinstance(raw, dict):
            raise AnalyzerError("Dify output is missing analysis_json")
        return LLMAnalysis.model_validate(raw)


class DemoAnalyzer:
    """Transparent deterministic fallback for local development and cached demo generation."""

    def analyze(self, ticket: TicketInput) -> LLMAnalysis:
        message = ticket.message
        rules = [
            (("API", "接口", "集成"), "integration", "open_api", "technical_support"),
            (("权限", "成员"), "permission", "member_permission", "implementation_support"),
            (("导入", "字段", "映射"), "data_consistency", "import_export", "qa_triage"),
            (("慢", "卡顿", "超时"), "performance", "project", "engineering_triage"),
            (("通知", "提醒"), "configuration", "notification", "customer_success"),
            (("建议", "希望", "能不能"), "feature_request", "task", "product_ops"),
        ]
        problem_type, product_area, owner = "bug", "project", "customer_success"
        for keywords, candidate_type, candidate_area, candidate_owner in rules:
            if any(keyword in message for keyword in keywords):
                problem_type, product_area, owner = candidate_type, candidate_area, candidate_owner
                break
        repeat_contacts = 2 if "两次" in message or "多次" in message else 0
        workflow_blocked = any(word in message for word in ("无法", "不能", "阻塞"))
        if "全公司" in message:
            scope = "organization"
        elif "团队" in message or "项目组" in message:
            scope = "team"
        else:
            scope = "individual"
        quote = message[: min(len(message), 80)]
        return LLMAnalysis.model_validate(
            {
                "summary": message[:80],
                "problem_type": problem_type,
                "product_area": product_area,
                "sentiment": (
                    "frustrated"
                    if any(x in message for x in ("多次", "严重", "着急"))
                    else "confused"
                ),
                "llm_owner_suggestion": owner,
                "root_cause_hypothesis": "需要结合配置和日志进一步确认",
                "impact_signals": {
                    "affected_scope": scope,
                    "workflow_blocked": workflow_blocked,
                    "data_loss_claimed": "数据丢失" in message,
                    "repeat_contacts": repeat_contacts,
                },
                "evidence_spans": [{"quote": quote}],
            }
        )
