import builtins
import sys
import types
from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import feedback_app.database as database
from feedback_app.analyzers import AnalyzerError
from feedback_app.clustering import pairwise_metrics, threshold_clusters
from feedback_app.config import Settings
from feedback_app.database import Base
from feedback_app.embeddings import SentenceTransformerEmbedder
from feedback_app.main import create_ticket
from feedback_app.models import Analysis, AnalysisJob, DemoSession
from feedback_app.reports import trend_for_dates
from feedback_app.routing import derive_severity, supplement_impact_signals
from feedback_app.schemas import ImpactSignals, Severity, TicketInput
from feedback_app.service import (
    cleanup_expired_sessions,
    create_demo_session,
    create_or_reuse_demo_session,
    enqueue_ticket,
    process_job,
    require_active_session,
)
from feedback_app.sop import build_sop_candidate
from feedback_app.workflow_suite import (
    WorkflowSuiteError,
    generate_cluster_narrative,
    generate_report_narrative,
    generate_sop_draft,
)


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def ticket_input(ticket_id: str, message: str = "Webhook failed") -> TicketInput:
    return TicketInput(
        ticket_id=ticket_id,
        message=message,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def add_analysis(
    db: Session,
    ticket_id: str,
    workflow_version: str,
    analysis_source: str = "demo_rules",
) -> Analysis:
    analysis = Analysis(
        ticket_id=ticket_id,
        payload={"summary": "Analysed", "analysis_source": analysis_source},
        problem_type="bug",
        product_area="task",
        suggested_owner="qa_triage",
        severity="low",
        needs_escalation=False,
        review_status="accepted",
        workflow_version=workflow_version,
        analysis_source=analysis_source,
    )
    db.add(analysis)
    db.flush()
    return analysis


def test_database_helpers_cover_non_sqlite_and_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert database._engine_kwargs("postgresql://example") == {"pool_pre_ping": True}

    closed: list[bool] = []

    class FakeSession:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(database, "SessionLocal", FakeSession)
    generator = database.get_db()
    assert isinstance(next(generator), FakeSession)
    generator.close()
    assert closed == [True]

    create_all_calls: list[object] = []
    monkeypatch.setattr(
        database.Base.metadata,
        "create_all",
        lambda bind: create_all_calls.append(bind),
    )
    database.init_db()
    assert create_all_calls == [database.engine]


def test_sentence_transformer_embedder_reports_missing_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("missing optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="embedding"):
        SentenceTransformerEmbedder("fake-model")


def test_sentence_transformer_embedder_normalizes_model_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("sentence_transformers")

    class FakeSentenceTransformer:
        def __init__(self, model_name: str) -> None:
            self.model_name = model_name

        def encode(
            self,
            texts: list[str],
            normalize_embeddings: bool,
            show_progress_bar: bool,
        ) -> list[list[float]]:
            assert normalize_embeddings is True
            assert show_progress_bar is False
            return [[1.0, 0.0] for _text in texts]

    module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    embedder = SentenceTransformerEmbedder("fake-model")

    assert embedder.encode(["a", "b"]).tolist() == [[1.0, 0.0], [1.0, 0.0]]


def test_reports_detect_rising_and_falling_trends() -> None:
    now = datetime(2025, 1, 15, tzinfo=UTC)

    assert trend_for_dates([now] * 5, now) == "rising"

    previous_week = [now.replace(day=5)] * 10
    current_week = [now.replace(day=14)] * 5
    assert trend_for_dates(previous_week + current_week, now) == "falling"

    flat_previous = [now.replace(day=5)] * 6
    flat_current = [now.replace(day=14)] * 6
    assert trend_for_dates(flat_previous + flat_current, now) == "stable"


def test_sop_candidate_requires_rising_or_high_severity_signal() -> None:
    assert build_sop_candidate(
        {
            "member_count": 5,
            "trend": "stable",
            "severity": "medium",
            "representative_ticket_ids": ["T1"],
            "title": "Login failures",
        }
    ) is None


def test_service_handles_expired_reuse_missing_session_and_empty_cleanup() -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    expired = DemoSession(
        ip_hash="ip",
        expires_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(expired)
    db.commit()

    replacement = create_or_reuse_demo_session(db, settings, expired.id, "ip")

    assert replacement.id != expired.id
    with pytest.raises(ValueError, match="demo_session_expired"):
        require_active_session(db, "missing")
    assert cleanup_expired_sessions(db) == 1
    assert cleanup_expired_sessions(db) == 0
    db.close()


def test_process_job_returns_existing_analysis_and_rejects_missing_ticket() -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    session = create_demo_session(db, settings)
    ticket, job = enqueue_ticket(db, settings, ticket_input("T-EXISTING"), session.id)
    existing = add_analysis(db, ticket.id, settings.workflow_version)
    db.commit()

    result = process_job(db, settings, job)

    assert result.id == existing.id
    assert db.get(AnalysisJob, job.id).status == "completed"

    missing_job = AnalysisJob(ticket_id="missing")
    db.add(missing_job)
    db.commit()
    with pytest.raises(ValueError, match="ticket_not_found"):
        process_job(db, settings, missing_job)
    db.close()


def test_process_job_strips_nested_cache_source() -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    session = create_demo_session(db, settings)
    message = "The same sanitized message"
    first, _first_job = enqueue_ticket(db, settings, ticket_input("T-CACHE-1", message), session.id)
    add_analysis(db, first.id, settings.workflow_version, "cache:demo_rules")
    second, second_job = enqueue_ticket(
        db,
        settings,
        ticket_input("T-CACHE-2", message),
        session.id,
    )
    db.commit()

    cached = process_job(db, settings, second_job)

    assert second.input_hash == first.input_hash
    assert cached.analysis_source == "cache:demo_rules"
    db.close()


def test_process_job_marks_third_analyzer_failure_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://", allow_demo_analyzer=False)
    session = create_demo_session(db, settings)
    _ticket, job = enqueue_ticket(db, settings, ticket_input("T-FAIL"), session.id)
    job.attempts = 2
    db.commit()

    def fail(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("feedback_app.service.DifyAnalyzer.analyze", fail)
    with pytest.raises(AnalyzerError):
        process_job(db, settings, job)

    db.refresh(job)
    assert job.status == "failed"
    db.close()


def test_create_ticket_reraises_unmatched_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_db()
    settings = Settings(database_url="sqlite://")
    session = create_demo_session(db, settings)

    def fail_enqueue(*_args, **_kwargs):
        raise IntegrityError("insert", {}, RuntimeError("constraint still failing"))

    monkeypatch.setattr("feedback_app.main.enqueue_ticket", fail_enqueue)

    with pytest.raises(IntegrityError):
        create_ticket(
            ticket_input("T-INTEGRITY"),
            db,
            settings,
            x_demo_session=session.id,
            idempotency_key="integrity",
        )
    db.close()


class WorkflowResponse:
    def __init__(self, outputs: dict, status: str = "succeeded") -> None:
        self.outputs = outputs
        self.status = status

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": {"status": self.status, "outputs": self.outputs}}


def workflow_settings() -> Settings:
    return Settings(
        dify_cluster_workflow_api_key="cluster-key",
        dify_sop_workflow_api_key="sop-key",
        dify_report_workflow_api_key="report-key",
    )


def test_workflow_suite_success_and_missing_output(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster_payload = {
        "title": "Login",
        "observation": "Login failures repeat",
        "pending_cause": None,
        "evidence_ticket_ids": ["T1"],
        "explanation": "Same symptom",
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: WorkflowResponse({"cluster_narrative_json": cluster_payload}),
    )

    result = generate_cluster_narrative(
        workflow_settings(),
        "C1",
        {"representative_tickets": [{"ticket_id": "T1"}]},
    )
    assert result.title == "Login"

    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: WorkflowResponse({}),
    )
    with pytest.raises(WorkflowSuiteError, match="missing"):
        generate_sop_draft(workflow_settings(), "C1", {"evidence_ticket_ids": ["T1"]})


def test_workflow_suite_rejects_missing_key_and_bad_report_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(WorkflowSuiteError, match="not configured"):
        generate_sop_draft(Settings(dify_sop_workflow_api_key=""), "C1", {})

    duplicate_payload = {
        "title": "Weekly",
        "executive_summary": "Summary",
        "observations": [
            {
                "cluster_id": "C1",
                "observation": "First",
                "evidence_ticket_ids": ["T1"],
                "recommended_action": "Review",
            },
            {
                "cluster_id": "C1",
                "observation": "Second",
                "evidence_ticket_ids": ["T1"],
                "recommended_action": "Review",
            },
        ],
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: WorkflowResponse({"report_narrative_json": duplicate_payload}),
    )
    context = {"clusters": [{"cluster_id": "C1", "evidence_ticket_ids": ["T1"]}]}
    with pytest.raises(WorkflowSuiteError, match="duplicate"):
        generate_report_narrative(workflow_settings(), "2025-W01", context)

    unknown_payload = {
        **duplicate_payload,
        "observations": [
            {
                "cluster_id": "C2",
                "observation": "Unknown",
                "evidence_ticket_ids": ["T2"],
                "recommended_action": "Review",
            }
        ],
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: WorkflowResponse({"report_narrative_json": unknown_payload}),
    )
    with pytest.raises(WorkflowSuiteError, match="not in deterministic input"):
        generate_report_narrative(workflow_settings(), "2025-W01", context)

    success_payload = {
        **duplicate_payload,
        "observations": [
            {
                "cluster_id": "C1",
                "observation": "Known",
                "evidence_ticket_ids": ["T1"],
                "recommended_action": "Review",
            }
        ],
    }
    monkeypatch.setattr(
        "feedback_app.workflow_suite.httpx.post",
        lambda *args, **kwargs: WorkflowResponse({"report_narrative_json": success_payload}),
    )
    report = generate_report_narrative(workflow_settings(), "2025-W01", context)
    assert report.observations[0].cluster_id == "C1"


def test_clustering_rejects_group_mismatch_and_handles_singleton_groups() -> None:
    vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]])

    with pytest.raises(ValueError, match="groups"):
        threshold_clusters(vectors, 0.8, groups=["only-one"])

    assert threshold_clusters(vectors, 0.8, groups=["a", "b"], linkage="complete") == [0, 1]
    assert pairwise_metrics(["same", "same"], [0, 1]) == {
        "precision": 0,
        "recall": 0.0,
        "f1": 0,
    }


def test_routing_and_schema_boundary_cases() -> None:
    organization = supplement_impact_signals(
        "\u5168\u516c\u53f8\u65e0\u6cd5\u6536\u5230\u901a\u77e5",
        ImpactSignals(),
    )
    assert organization.affected_scope == "organization"
    assert derive_severity(ImpactSignals(data_loss_claimed=True)) == Severity.CRITICAL

    with pytest.raises(ValueError, match="message"):
        TicketInput(
            ticket_id="T-BLANK",
            message="   ",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
