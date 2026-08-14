import asyncio
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from feedback_app.config import Settings, get_settings
from feedback_app.database import Base, get_db
from feedback_app.main import app, lifespan, unhandled_exception
from feedback_app.models import (
    Analysis,
    IssueCluster,
    SOPCandidate,
    Ticket,
    WeeklyReport,
)

ClientFactory = Callable[..., TestClient]


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client_factory(db: Session) -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def factory(**settings_kwargs) -> TestClient:
        app.dependency_overrides.clear()

        def override_db() -> Iterator[Session]:
            yield db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: Settings(
            database_url="sqlite://",
            **settings_kwargs,
        )
        client = TestClient(app)
        clients.append(client)
        return client

    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        for client in clients:
            client.close()


def ticket_payload(ticket_id: str = "T-1") -> dict:
    return {
        "ticket_id": ticket_id,
        "user_type": "member",
        "channel": "support",
        "message": "Webhook delivery is blocked for the whole team",
        "created_at": "2025-01-01T00:00:00Z",
        "current_status": "open",
    }


def create_session(client: TestClient, client_id: str = "browser-a") -> str:
    response = client.post("/v1/demo/sessions", headers={"X-Demo-Client": client_id})
    assert response.status_code == 201
    return response.json()["session_id"]


def add_ticket(db: Session, external_id: str, session_id: str | None = None) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        session_id=session_id,
        user_type="member",
        channel="support",
        message="A persisted support ticket",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        current_status="open",
        input_hash=f"hash-{external_id}",
        source="live" if session_id else "seed",
    )
    db.add(ticket)
    db.flush()
    return ticket


def add_analysis(db: Session, ticket: Ticket) -> Analysis:
    analysis = Analysis(
        ticket_id=ticket.id,
        payload={"summary": "Ticket analysed"},
        problem_type="bug",
        product_area="task",
        suggested_owner="qa_triage",
        severity="low",
        needs_escalation=False,
        review_status="accepted",
        workflow_version="v1",
        analysis_source="test",
    )
    db.add(analysis)
    db.flush()
    return analysis


def test_health_metrics_and_demo_session_reuse(client_factory: ClientFactory) -> None:
    client = client_factory()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "feedback-api"}

    session_id = create_session(client)
    reused = client.post(
        "/v1/demo/sessions",
        headers={"X-Demo-Client": "browser-a", "X-Demo-Session": session_id},
    )
    assert reused.status_code == 201
    assert reused.json()["reused"] is True

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "feedback_tickets" in metrics.text


def test_lifespan_initializes_database(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("feedback_app.main.init_db", lambda: calls.append("init"))

    async def run_lifespan() -> None:
        async with lifespan(app):
            calls.append("inside")

    asyncio.run(run_lifespan())

    assert calls == ["init", "inside"]


def test_create_ticket_requires_session_and_idempotency(
    client_factory: ClientFactory,
    db: Session,
) -> None:
    client = client_factory()

    assert client.post("/v1/tickets", json=ticket_payload()).status_code == 401
    session_id = create_session(client)

    missing_key = client.post(
        "/v1/tickets",
        json=ticket_payload(),
        headers={"X-Demo-Session": session_id},
    )
    assert missing_key.status_code == 400
    assert missing_key.json()["detail"] == "missing_idempotency_key"

    long_key = client.post(
        "/v1/tickets",
        json=ticket_payload(),
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "x" * 129},
    )
    assert long_key.status_code == 400
    assert long_key.json()["detail"] == "idempotency_key_too_long"

    created = client.post(
        "/v1/tickets",
        json=ticket_payload("T-100"),
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "ticket-100"},
    )
    assert created.status_code == 202
    assert created.json()["reused"] is False

    reused = client.post(
        "/v1/tickets",
        json=ticket_payload("T-100"),
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "ticket-100"},
    )
    assert reused.status_code == 202
    assert reused.json()["reused"] is True
    assert db.query(Ticket).count() == 1


def test_ticket_quota_errors(client_factory: ClientFactory) -> None:
    limited_client = client_factory(live_session_daily_limit=0)
    session_id = create_session(limited_client)
    response = limited_client.post(
        "/v1/tickets",
        json=ticket_payload("T-LIMIT"),
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "limit"},
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "session_daily_limit_reached"


def test_ticket_rejects_invalid_session_and_global_quota(
    client_factory: ClientFactory,
) -> None:
    client = client_factory()
    invalid_session = client.post(
        "/v1/tickets",
        json=ticket_payload("T-INVALID-SESSION"),
        headers={"X-Demo-Session": "missing", "Idempotency-Key": "invalid"},
    )
    assert invalid_session.status_code == 401
    assert invalid_session.json()["detail"] == "demo_session_expired"

    quota_client = client_factory(live_global_daily_limit=0)
    session_id = create_session(quota_client, "quota")
    global_quota = quota_client.post(
        "/v1/tickets",
        json=ticket_payload("T-GLOBAL-LIMIT"),
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "global-limit"},
    )
    assert global_quota.status_code == 429
    assert global_quota.json()["detail"] == "global_daily_limit_reached"


def test_ticket_list_detail_and_job_visibility(
    client_factory: ClientFactory,
    db: Session,
) -> None:
    client = client_factory()
    owner_session = create_session(client, "owner")
    other_session = create_session(client, "other")
    public_ticket = add_ticket(db, "PUBLIC")
    add_analysis(db, public_ticket)
    db.commit()

    created = client.post(
        "/v1/tickets",
        json=ticket_payload("PRIVATE"),
        headers={"X-Demo-Session": owner_session, "Idempotency-Key": "private"},
    )
    body = created.json()
    private_ticket_id = body["ticket_id"]
    job_id = body["job_id"]

    anonymous_list = client.get("/v1/tickets?limit=0")
    assert anonymous_list.status_code == 200
    assert [row["ticket_id"] for row in anonymous_list.json()] == ["PUBLIC"]

    owner_list = client.get("/v1/tickets", headers={"X-Demo-Session": owner_session})
    assert {row["ticket_id"] for row in owner_list.json()} == {"PUBLIC", "PRIVATE"}

    detail = client.get(f"/v1/tickets/{public_ticket.id}")
    assert detail.status_code == 200
    assert detail.json()["analysis"] == {"summary": "Ticket analysed"}

    hidden = client.get(
        f"/v1/tickets/{private_ticket_id}",
        headers={"X-Demo-Session": other_session},
    )
    assert hidden.status_code == 404

    job = client.get(f"/v1/jobs/{job_id}", headers={"X-Demo-Session": owner_session})
    assert job.status_code == 200
    assert job.json()["ticket_id"] == private_ticket_id

    assert client.get("/v1/jobs/missing").status_code == 404


def test_import_tickets_validates_csv_and_skips_duplicates(
    client_factory: ClientFactory,
    db: Session,
) -> None:
    client = client_factory(max_csv_rows=3, max_csv_bytes=512)
    session_id = create_session(client)
    csv_body = "ticket_id,message,created_at\nCSV-1,First issue,2025-01-01T00:00:00Z\n"
    csv_body += "CSV-1,Duplicate issue,2025-01-01T00:00:00Z\n"

    response = client.post(
        "/v1/imports",
        headers={"X-Demo-Session": session_id},
        files={"file": ("tickets.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert db.query(Ticket).filter(Ticket.external_id == "CSV-1").count() == 1

    missing_columns = client.post(
        "/v1/imports",
        headers={"X-Demo-Session": session_id},
        files={"file": ("tickets.csv", "ticket_id,message\nCSV-2,No date\n", "text/csv")},
    )
    assert missing_columns.status_code == 400
    assert missing_columns.json()["detail"] == "csv_missing_required_columns"

    invalid_utf8 = client.post(
        "/v1/imports",
        headers={"X-Demo-Session": session_id},
        files={"file": ("tickets.csv", b"\xff\xfe\xfd", "text/csv")},
    )
    assert invalid_utf8.status_code == 400
    assert invalid_utf8.json()["detail"] == "invalid_utf8_csv"

    too_large_client = client_factory(max_csv_bytes=8)
    too_large_session = create_session(too_large_client, "large")
    too_large = too_large_client.post(
        "/v1/imports",
        headers={"X-Demo-Session": too_large_session},
        files={"file": ("tickets.csv", csv_body, "text/csv")},
    )
    assert too_large.status_code == 413

    empty = client.post(
        "/v1/imports",
        headers={"X-Demo-Session": session_id},
        files={"file": ("tickets.csv", "", "text/csv")},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "csv_row_limit"


def test_cluster_sop_weekly_and_review_endpoints(
    client_factory: ClientFactory,
    db: Session,
) -> None:
    client = client_factory()
    session_id = create_session(client)
    ticket = add_ticket(db, "REVIEW", session_id)
    cluster = IssueCluster(
        title="Login failures",
        summary="Users cannot log in",
        member_count=5,
        severity="high",
        trend="rising",
        suggested_owner="technical_support",
        centroid=[0.1, 0.2],
        representative_ticket_ids=[ticket.external_id],
        evidence=[{"quote": "cannot log in"}],
        narrative_source="test",
        narrative_workflow_version="cluster-v1",
    )
    db.add(cluster)
    db.flush()
    candidate = SOPCandidate(
        cluster_id=cluster.id,
        title="Login SOP",
        payload={"title": "Login SOP", "steps": ["Check SSO"]},
        status="pending_review",
        generation_source="test",
        workflow_version="sop-v1",
    )
    report = WeeklyReport(
        week_start=datetime(2025, 1, 6, tzinfo=UTC),
        payload={"top_issue": "login"},
        markdown="# Weekly",
        generation_source="test",
        workflow_version="report-v1",
    )
    db.add_all([candidate, report])
    db.commit()

    assert client.get("/v1/clusters/missing").status_code == 404
    cluster_detail = client.get(f"/v1/clusters/{cluster.id}")
    assert cluster_detail.status_code == 200
    assert cluster_detail.json()["title"] == "Login failures"

    invalid_review = client.patch(
        f"/v1/sop-candidates/{candidate.id}",
        headers={"X-Demo-Session": session_id},
        json={"status": "unknown"},
    )
    assert invalid_review.status_code == 400

    created_review = client.patch(
        f"/v1/sop-candidates/{candidate.id}",
        headers={"X-Demo-Session": session_id},
        json={"status": "accepted", "note": "Looks good"},
    )
    assert created_review.status_code == 200
    assert created_review.json()["scope"] == "session_only"

    updated_review = client.patch(
        f"/v1/sop-candidates/{candidate.id}",
        headers={"X-Demo-Session": session_id},
        json={"status": "rejected", "note": "Needs rewrite"},
    )
    assert updated_review.json()["status"] == "rejected"

    candidates = client.get("/v1/sop-candidates", headers={"X-Demo-Session": session_id})
    assert candidates.status_code == 200
    assert candidates.json()[0]["session_status"] == "rejected"

    missing_candidate = client.patch(
        "/v1/sop-candidates/missing",
        headers={"X-Demo-Session": session_id},
        json={"status": "accepted"},
    )
    assert missing_candidate.status_code == 404

    weekly = client.get("/v1/reports/weekly")
    assert weekly.status_code == 200
    assert weekly.json()["markdown"] == "# Weekly"

    ticket_review = client.patch(
        f"/v1/tickets/{ticket.id}/review",
        headers={"X-Demo-Session": session_id},
        json={"status": "corrected", "corrected_owner": "technical_support"},
    )
    assert ticket_review.status_code == 200
    assert ticket_review.json()["corrections"] == {"corrected_owner": "technical_support"}

    updated_ticket_review = client.patch(
        f"/v1/tickets/{ticket.id}/review",
        headers={"X-Demo-Session": session_id},
        json={"status": "accepted", "note": "Verified"},
    )
    assert updated_ticket_review.json()["status"] == "accepted"


def test_weekly_report_missing(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/v1/reports/weekly")
    assert response.status_code == 404
    assert response.json()["detail"] == "weekly_report_not_found"


def test_evaluation_endpoints_read_artifacts(
    client_factory: ClientFactory,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = client_factory()
    monkeypatch.chdir(tmp_path)

    assert client.get("/v1/evaluation").status_code == 404
    assert client.get("/v1/evaluation/candidate").status_code == 404
    assert client.get("/v1/evaluation/suite").status_code == 404

    evaluation_dir = tmp_path / "artifacts" / "evaluation"
    evaluation_dir.mkdir(parents=True)
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps({"score": 1}),
        encoding="utf-8",
    )
    assert client.get("/v1/evaluation").json() == {"score": 1}

    candidate_dir = tmp_path / "artifacts" / "evaluation-v2-candidate"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "status.json").write_text(
        json.dumps({"state": "running"}),
        encoding="utf-8",
    )
    assert client.get("/v1/evaluation/candidate").json() == {"state": "running"}
    (candidate_dir / "evaluation.json").write_text(
        json.dumps({"state": "complete"}),
        encoding="utf-8",
    )
    assert client.get("/v1/evaluation/candidate").json() == {"state": "complete"}

    structure_dir = tmp_path / "artifacts" / "evaluation-v7-candidate"
    content_dir = tmp_path / "artifacts" / "workflow-suite-v1-candidate"
    structure_dir.mkdir(parents=True)
    content_dir.mkdir(parents=True)
    (structure_dir / "evaluation.json").write_text(
        json.dumps({"dataset_version": "v7", "quality_gates": {"all_measured_passed": True}}),
        encoding="utf-8",
    )
    (structure_dir / "promotion-record.json").write_text(
        json.dumps({"decision": "candidate", "scope": "offline"}),
        encoding="utf-8",
    )
    (content_dir / "evaluation.json").write_text(
        json.dumps({"quality_gates": {"all_passed": True}}),
        encoding="utf-8",
    )
    suite = client.get("/v1/evaluation/suite")
    assert suite.status_code == 200
    assert suite.json()["overall_passed"] is True


def test_unhandled_exception_response() -> None:
    response = asyncio.run(unhandled_exception(Request({"type": "http"}), RuntimeError("boom")))

    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": "internal_error", "type": "RuntimeError"}
