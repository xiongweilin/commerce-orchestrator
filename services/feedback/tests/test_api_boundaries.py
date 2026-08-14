from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from feedback_app import main as main_module
from feedback_app.config import Settings, get_settings
from feedback_app.database import Base, get_db
from feedback_app.main import app


@pytest.fixture
def client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    settings = Settings(
        database_url="sqlite://",
        allow_demo_analyzer=True,
        live_session_daily_limit=20,
        live_ip_daily_limit=100,
        live_global_daily_limit=100,
    )

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.close()


def new_session(client: TestClient) -> str:
    response = client.post("/v1/demo/sessions")
    assert response.status_code == 201
    return response.json()["session_id"]


def ticket_payload(ticket_id: str) -> dict:
    return {
        "ticket_id": ticket_id,
        "message": "我们整个项目组都收不到到期提醒",
        "created_at": datetime.now(UTC).isoformat(),
    }


def test_candidate_evaluation_exposes_scored_unpromoted_state(client: TestClient) -> None:
    response = client.get("/v1/evaluation/candidate")
    assert response.status_code == 200
    assert response.json()["evaluation_state"] == "candidate_scored_unpromoted"
    assert response.json()["quality_gates"]["all_measured_passed"] is False


def test_suite_evaluation_keeps_component_and_overall_status_separate(
    client: TestClient,
) -> None:
    response = client.get("/v1/evaluation/suite")
    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluation_state"] == "promoted_for_portfolio_demo"
    assert payload["overall_passed"] is True
    assert payload["promotion"]["quality_gates_all_passed"] is True
    assert payload["content_workflows"]["quality_gates"]["all_passed"] is True
    assert (
        payload["structure_clustering"]["quality_gates"]["all_measured_passed"]
        is True
    )


def test_session_creation_reuses_active_header(client: TestClient) -> None:
    session_id = new_session(client)
    response = client.post("/v1/demo/sessions", headers={"X-Demo-Session": session_id})
    assert response.status_code == 201
    assert response.json() == {
        "session_id": session_id,
        "expires_at": response.json()["expires_at"],
        "reused": True,
    }


def test_idempotency_key_reuses_original_request(client: TestClient) -> None:
    session_id = new_session(client)
    headers = {"X-Demo-Session": session_id, "Idempotency-Key": "same-operation"}
    first = client.post("/v1/tickets", headers=headers, json=ticket_payload("T001"))
    second = client.post("/v1/tickets", headers=headers, json=ticket_payload("T999"))
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["reused"] is True
    assert second.json()["ticket_id"] == first.json()["ticket_id"]


def test_idempotency_recovers_from_concurrent_unique_constraint_race(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = new_session(client)
    real_enqueue = main_module.enqueue_ticket

    def raced_enqueue(*args, **kwargs):
        real_enqueue(*args, **kwargs)
        raise IntegrityError("concurrent insert", {}, RuntimeError("unique"))

    monkeypatch.setattr(main_module, "enqueue_ticket", raced_enqueue)
    response = client.post(
        "/v1/tickets",
        headers={"X-Demo-Session": session_id, "Idempotency-Key": "raced"},
        json=ticket_payload("T-RACE"),
    )
    assert response.status_code == 202
    assert response.json()["reused"] is True
    assert response.json()["ticket_id"]


def test_new_cookies_cannot_bypass_client_daily_quota(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://",
        allow_demo_analyzer=True,
        live_session_daily_limit=20,
        live_ip_daily_limit=1,
        live_global_daily_limit=100,
    )
    first_session = new_session(client)
    first = client.post(
        "/v1/tickets",
        headers={"X-Demo-Session": first_session, "Idempotency-Key": "first"},
        json=ticket_payload("T010"),
    )
    second_session = new_session(client)
    second = client.post(
        "/v1/tickets",
        headers={"X-Demo-Session": second_session, "Idempotency-Key": "second"},
        json=ticket_payload("T011"),
    )
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"] == "client_daily_limit_reached"


def test_private_ticket_and_job_are_not_visible_to_another_session(
    client: TestClient,
) -> None:
    owner = new_session(client)
    stranger = new_session(client)
    created = client.post(
        "/v1/tickets",
        headers={"X-Demo-Session": owner, "Idempotency-Key": "private"},
        json=ticket_payload("T002"),
    ).json()
    stranger_headers = {"X-Demo-Session": stranger}
    assert client.get(
        f"/v1/tickets/{created['ticket_id']}", headers=stranger_headers
    ).status_code == 404
    assert client.get(
        f"/v1/jobs/{created['job_id']}", headers=stranger_headers
    ).status_code == 404
    assert client.patch(
        f"/v1/tickets/{created['ticket_id']}/review",
        headers=stranger_headers,
        json={"status": "accepted"},
    ).status_code == 404
