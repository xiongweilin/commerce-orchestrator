"""Unit tests for pipeline helper functions."""

from unittest.mock import MagicMock

from feedback_app.config import Settings
from feedback_app.models import Analysis, Ticket
from feedback_app.pipeline import (
    _extract_cluster_features,
    _group_indices_by_label,
    _resolve_blocking_groups,
    _truncate_cluster_tables,
)

# ---------------------------------------------------------------------------
# _resolve_blocking_groups
# ---------------------------------------------------------------------------

def test_resolve_blocking_groups_disabled_returns_product_areas():
    settings = Settings(cluster_block_by_problem_type=False)
    result = _resolve_blocking_groups(settings, ["A", "B"], ["X", "Y"])
    assert result == ["A", "B"]


def test_resolve_blocking_groups_none_settings_returns_product_areas():
    result = _resolve_blocking_groups(None, ["A"], ["X"])
    assert result == ["A"]


def test_resolve_blocking_groups_enabled_combines_with_pipe():
    settings = Settings(cluster_block_by_problem_type=True)
    result = _resolve_blocking_groups(settings, ["Login", "Payment"], ["Bug", "Feature"])
    assert result == ["Login|Bug", "Payment|Feature"]


# ---------------------------------------------------------------------------
# _group_indices_by_label
# ---------------------------------------------------------------------------

def test_group_indices_by_label_groups_by_value():
    result = _group_indices_by_label(["a", "b", "a", "c", "b"])
    assert result == {"a": [0, 2], "b": [1, 4], "c": [3]}


def test_group_indices_by_label_empty_input():
    result = _group_indices_by_label([])
    assert result == {}


def test_group_indices_by_label_single_label():
    result = _group_indices_by_label(["x", "x", "x"])
    assert result == {"x": [0, 1, 2]}


# ---------------------------------------------------------------------------
# _extract_cluster_features
# ---------------------------------------------------------------------------

def test_extract_cluster_features_extracts_texts_and_areas():
    ticket_a = MagicMock(spec=Ticket)
    ticket_a.message = "Login page timeout"
    ticket_b = MagicMock(spec=Ticket)
    ticket_b.message = "Payment fails"
    analysis_a = MagicMock(spec=Analysis)
    analysis_a.payload = {"issue_signature": "slow login", "summary": "login is slow"}
    analysis_a.product_area = "Login"
    analysis_a.problem_type = "Bug"
    analysis_b = MagicMock(spec=Analysis)
    analysis_b.payload = {"summary": "payment broken"}
    analysis_b.product_area = "Payment"
    analysis_b.problem_type = "Bug"

    rows = [(ticket_a, analysis_a), (ticket_b, analysis_b)]
    texts, summaries, areas, ptypes = _extract_cluster_features(rows)

    assert texts == ["Login page timeout", "Payment fails"]
    assert summaries == ["slow login", "payment broken"]
    assert areas == ["Login", "Payment"]
    assert ptypes == ["Bug", "Bug"]


def test_extract_cluster_features_falls_back_to_summary():
    ticket = MagicMock(spec=Ticket)
    ticket.message = "Crash on startup"
    analysis = MagicMock(spec=Analysis)
    analysis.payload = {"summary": "app crashes"}
    analysis.product_area = "General"
    analysis.problem_type = "Bug"

    texts, summaries, _, _ = _extract_cluster_features([(ticket, analysis)])
    assert summaries == ["app crashes"]
# ---------------------------------------------------------------------------
# DB-backed tests using in-memory SQLite
# ---------------------------------------------------------------------------


def test_truncate_cluster_tables_deletes_all_cluster_data():
    """_truncate_cluster_tables executes DELETE on cluster tables."""
    from unittest.mock import MagicMock
    db = MagicMock()
    db.execute.return_value = None
    db.flush.return_value = None
    _truncate_cluster_tables(db)
    assert db.execute.call_count == 3
    db.flush.assert_called_once()