from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from feedback_app.config import Settings
from feedback_app.database import Base
from feedback_app.models import (
    Analysis,
    ClusterMember,
    IssueCluster,
    SOPCandidate,
    Ticket,
    WeeklyReport,
)
from feedback_app.pipeline import (
    _build_single_cluster,
    _truncate_cluster_tables,
    rebuild_clusters,
    rebuild_weekly_report,
)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _ticket(db, eid, msg="test"):
    t = Ticket(id=eid, external_id=eid, message=msg, input_hash=eid,
               created_at=datetime(2025, 1, 1, tzinfo=UTC))
    db.add(t)
    db.flush()
    return t


def _analysis(db, tid, summary, owner="ops", severity="medium",
              area="General", ptype="Bug", review="pending"):
    a = Analysis(ticket_id=tid, payload={"summary": summary},
                 problem_type=ptype, product_area=area,
                 suggested_owner=owner, severity=severity,
                 review_status=review, workflow_version="v1",
                 analysis_source="test")
    db.add(a)
    db.flush()
    return a


class FakeEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray([[1.0, 0.0] for _ in texts], dtype=float)


def test_build_single_cluster_creates_cluster_and_members():
    db = make_db()
    s = Settings(cluster_block_by_problem_type=False)
    t1 = _ticket(db, "T1")
    t2 = _ticket(db, "T2")
    a1 = _analysis(db, t1.id, "Login timeout")
    a2 = _analysis(db, t2.id, "Login freeze")
    rows = [(t1, a1), (t2, a2)]
    vecs = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=float)
    _build_single_cluster(db, rows, vecs, [0, 1], s)
    db.commit()
    c = db.query(IssueCluster).first()
    assert c is not None
    assert c.member_count == 2
    assert db.query(ClusterMember).count() == 2
    db.close()


def test_truncate_cluster_clears_all():
    db = make_db()
    t = _ticket(db, "T1")
    a = _analysis(db, t.id, "test")
    rows = [(t, a)]
    _build_single_cluster(db, rows, np.array([[1.0, 2.0]], dtype=float), [0], Settings())
    db.commit()
    assert db.query(IssueCluster).count() == 1
    _truncate_cluster_tables(db)
    assert db.query(IssueCluster).count() == 0
    db.close()


def test_rebuild_clusters_returns_empty_when_no_live_tickets():
    db = make_db()
    assert rebuild_clusters(db, FakeEmbedder(), 0.75, Settings()) == []
    db.close()


def test_rebuild_clusters_groups_live_ticket_analyses():
    db = make_db()
    t1 = _ticket(db, "T1", "same failure")
    t2 = _ticket(db, "T2", "same failure again")
    _analysis(db, t1.id, "Same failure", severity="high")
    _analysis(db, t2.id, "Same failure", severity="high")
    db.commit()

    clusters = rebuild_clusters(db, FakeEmbedder(), 0.75, Settings(cluster_raw_text_weight=1.0))

    assert len(clusters) == 1
    assert clusters[0].member_count == 2
    assert db.query(ClusterMember).count() == 2
    db.close()


def test_build_cluster_uses_dify_narrative_and_sop_when_available(monkeypatch):
    db = make_db()
    settings = Settings(
        dify_cluster_workflow_api_key="cluster-key",
        dify_sop_workflow_api_key="sop-key",
    )
    pairs = []
    for index in range(5):
        ticket = _ticket(db, f"T{index + 1}")
        analysis = _analysis(db, ticket.id, f"Login timeout {index}", severity="high")
        pairs.append((ticket, analysis))

    monkeypatch.setattr(
        "feedback_app.pipeline.generate_cluster_narrative",
        lambda *_args, **_kw: SimpleNamespace(
            title="Dify title", observation="Dify observation", pending_cause="probable cause"
        ),
    )
    monkeypatch.setattr(
        "feedback_app.pipeline.generate_sop_draft",
        lambda *_args, **_kw: SimpleNamespace(
            title="Dify SOP",
            applicable_when="When high severity repeats",
            steps=["Check config", "Collect logs", "Escalate"],
            pending_cause="probable cause",
            evidence_ticket_ids=["T1", "T2", "T3"],
        ),
    )

    cluster = _build_single_cluster(
        db, pairs, np.asarray([[1.0, 0.0] for _ in pairs]), list(range(len(pairs))), settings
    )
    sop = db.query(SOPCandidate).one()

    assert cluster.title == "Dify title"
    assert cluster.narrative_source == "dify"
    assert sop.title == "Dify SOP"
    assert sop.generation_source == "dify"


def test_build_cluster_falls_back_when_sop_workflow_fails(monkeypatch):
    db = make_db()
    settings = Settings(dify_sop_workflow_api_key="sop-key")
    pairs = []
    for index in range(5):
        ticket = _ticket(db, f"T{index + 1}")
        analysis = _analysis(db, ticket.id, f"Login timeout {index}", severity="high")
        pairs.append((ticket, analysis))

    def fail_sop(*_args, **_kwargs):
        raise ValueError("bad workflow payload")

    monkeypatch.setattr("feedback_app.pipeline.generate_sop_draft", fail_sop)

    _build_single_cluster(
        db, pairs, np.asarray([[1.0, 0.0] for _ in pairs]), list(range(len(pairs))), settings
    )
    sop = db.query(SOPCandidate).one()

    assert sop.generation_source == "deterministic"
    assert sop.workflow_version is None


def test_rebuild_weekly_report_creates_and_updates_existing_report():
    db = make_db()
    as_of = datetime(2026, 7, 6, 12, tzinfo=UTC)
    t1 = _ticket(db, "T1")
    t2 = _ticket(db, "T2")
    _analysis(db, t1.id, "Login timeout", severity="high")
    _analysis(db, t2.id, "Login freeze", severity="medium")
    db.add(
        IssueCluster(
            id="cluster-1",
            title="Login timeout",
            member_count=2,
            severity="high",
            trend="stable",
            suggested_owner="ops",
            centroid=[1.0, 0.0],
            representative_ticket_ids=["T1", "T2"],
        )
    )
    db.commit()

    first = rebuild_weekly_report(
        db, as_of=as_of, settings=Settings(dify_report_workflow_api_key="")
    )
    first_text = first.payload["observations"][0]["text"]
    db.get(IssueCluster, "cluster-1").title = "Updated title"
    second = rebuild_weekly_report(
        db, as_of=as_of, settings=Settings(dify_report_workflow_api_key="")
    )

    assert first.id == second.id
    assert db.query(WeeklyReport).count() == 1
    assert second.payload["observations"][0]["text"] != first_text


def test_rebuild_weekly_report_uses_dify_narrative(monkeypatch):
    db = make_db()
    as_of = datetime(2026, 7, 6, 12, tzinfo=UTC)
    ticket = _ticket(db, "T1")
    _analysis(db, ticket.id, "Login timeout", severity="high")
    db.add(
        IssueCluster(
            id="cluster-1",
            title="Login timeout",
            member_count=1,
            severity="high",
            trend="rising",
            suggested_owner="ops",
            centroid=[1.0, 0.0],
            representative_ticket_ids=["T1"],
        )
    )
    db.commit()
    monkeypatch.setattr(
        "feedback_app.pipeline.generate_report_narrative",
        lambda *_args, **_kw: SimpleNamespace(
            title="Dify report",
            executive_summary="Dify summary",
            observations=[
                SimpleNamespace(
                    cluster_id="cluster-1",
                    observation="Dify observation",
                    evidence_ticket_ids=["T1"],
                    pending_cause=None,
                    recommended_action="Check logs",
                )
            ],
        ),
    )

    report = rebuild_weekly_report(
        db, as_of=as_of, settings=Settings(dify_report_workflow_api_key="report-key")
    )

    assert report.generation_source == "dify"
    assert report.payload["title"] == "Dify report"
    assert report.payload["observations"][0]["text"] == "Dify observation"


def test_rebuild_weekly_report_falls_back_when_dify_narrative_fails(monkeypatch):
    db = make_db()
    as_of = datetime(2026, 7, 6, 12, tzinfo=UTC)
    ticket = _ticket(db, "T1")
    _analysis(db, ticket.id, "Login timeout", severity="high")
    db.add(
        IssueCluster(
            id="cluster-1",
            title="Login timeout",
            member_count=1,
            severity="high",
            trend="rising",
            suggested_owner="ops",
            centroid=[1.0, 0.0],
            representative_ticket_ids=["T1"],
        )
    )
    db.commit()

    def fail_report(*_args, **_kwargs):
        raise ValueError("bad workflow payload")

    monkeypatch.setattr("feedback_app.pipeline.generate_report_narrative", fail_report)

    report = rebuild_weekly_report(
        db, as_of=as_of, settings=Settings(dify_report_workflow_api_key="report-key")
    )

    assert report.generation_source == "deterministic"
    assert report.payload["observations"][0]["cluster_id"] == "cluster-1"
