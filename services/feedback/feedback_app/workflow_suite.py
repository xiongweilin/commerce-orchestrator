import json

import httpx
from pydantic import BaseModel, Field

from .config import Settings


class WorkflowSuiteError(RuntimeError):  # noqa: S5713 (intentional marker exception - caught and re-raised before generic handlers)
    pass


class ClusterNarrativeDraft(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    observation: str = Field(min_length=1, max_length=160)
    pending_cause: str | None = Field(default=None, max_length=160)
    evidence_ticket_ids: list[str] = Field(min_length=1, max_length=5)
    explanation: str = Field(min_length=1, max_length=160)


class SOPDraft(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    applicable_when: str = Field(min_length=1, max_length=160)
    steps: list[str] = Field(min_length=2, max_length=6)
    pending_cause: str | None = Field(default=None, max_length=160)
    evidence_ticket_ids: list[str] = Field(min_length=1, max_length=5)


class ReportObservationDraft(BaseModel):
    cluster_id: str = Field(min_length=1, max_length=64)
    observation: str = Field(min_length=1, max_length=160)
    evidence_ticket_ids: list[str] = Field(min_length=1, max_length=5)
    pending_cause: str | None = Field(default=None, max_length=160)
    recommended_action: str = Field(min_length=1, max_length=160)


class ReportNarrativeDraft(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    executive_summary: str = Field(min_length=1, max_length=240)
    observations: list[ReportObservationDraft] = Field(min_length=1, max_length=5)


def _run_workflow(
    settings: Settings,
    api_key: str,
    inputs: dict,
    output_name: str,
) -> dict:
    if not api_key:
        raise WorkflowSuiteError(f"{output_name} workflow key is not configured")
    try:
        response = httpx.post(
            f"{settings.dify_base_url.rstrip('/')}/workflows/run",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "inputs": inputs,
                "response_mode": "blocking",
                "user": "feedback-content-rebuild",
            },
            timeout=settings.dify_timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data", {})
        if data.get("status") != "succeeded":
            raise WorkflowSuiteError(f"Dify workflow failed: {data.get('error') or 'unknown'}")
        raw = data.get("outputs", {}).get(output_name)
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise WorkflowSuiteError(f"Dify output is missing {output_name}")
        return raw
    except WorkflowSuiteError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise WorkflowSuiteError(f"{output_name} workflow request failed") from exc


def _assert_evidence_subset(actual: list[str], allowed: set[str]) -> None:
    if len(actual) != len(set(actual)) or any(item not in allowed for item in actual):
        raise WorkflowSuiteError("workflow evidence is not a subset of deterministic input")


def generate_cluster_narrative(
    settings: Settings,
    cluster_id: str,
    context: dict,
) -> ClusterNarrativeDraft:
    raw = _run_workflow(
        settings,
        settings.dify_cluster_workflow_api_key,
        {
            "cluster_id": cluster_id,
            "cluster_context_json": json.dumps(context, ensure_ascii=False),
        },
        "cluster_narrative_json",
    )
    result = ClusterNarrativeDraft.model_validate(raw)
    allowed = {item["ticket_id"] for item in context["representative_tickets"]}
    _assert_evidence_subset(result.evidence_ticket_ids, allowed)
    return result


def generate_sop_draft(settings: Settings, cluster_id: str, context: dict) -> SOPDraft:
    raw = _run_workflow(
        settings,
        settings.dify_sop_workflow_api_key,
        {
            "cluster_id": cluster_id,
            "sop_context_json": json.dumps(context, ensure_ascii=False),
        },
        "sop_draft_json",
    )
    result = SOPDraft.model_validate(raw)
    _assert_evidence_subset(result.evidence_ticket_ids, set(context["evidence_ticket_ids"]))
    return result


def generate_report_narrative(
    settings: Settings,
    report_period: str,
    context: dict,
) -> ReportNarrativeDraft:
    raw = _run_workflow(
        settings,
        settings.dify_report_workflow_api_key,
        {
            "report_period": report_period,
            "report_context_json": json.dumps(context, ensure_ascii=False),
        },
        "report_narrative_json",
    )
    result = ReportNarrativeDraft.model_validate(raw)
    allowed = {
        item["cluster_id"]: set(item["evidence_ticket_ids"])
        for item in context["clusters"]
    }
    if len(result.observations) != len({item.cluster_id for item in result.observations}):
        raise WorkflowSuiteError("report contains duplicate cluster ids")
    for item in result.observations:
        if item.cluster_id not in allowed:
            raise WorkflowSuiteError("report cluster id is not in deterministic input")
        _assert_evidence_subset(item.evidence_ticket_ids, allowed[item.cluster_id])
    return result
