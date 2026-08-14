import json
from collections import Counter

from tools.generate_dataset import build_rows


def test_dataset_has_planned_split_and_class_support() -> None:
    rows = build_rows()
    development = [row for row in rows if row["split"] == "development"]
    holdout = [row for row in rows if row["split"] == "holdout"]
    assert len(rows) == 240
    assert len(development) == 180
    assert len(holdout) == 60
    problem_support = Counter(row["gold_problem_type"] for row in holdout)
    area_support = Counter(row["gold_product_area"] for row in holdout)
    assert min(problem_support.values()) >= 5
    assert min(area_support.values()) >= 5


def test_runtime_fields_do_not_require_gold_labels() -> None:
    row = build_rows()[0]
    runtime_fields = {
        "ticket_id",
        "user_type",
        "channel",
        "message",
        "created_at",
        "current_status",
    }
    assert runtime_fields.issubset(row)


def test_impact_signals_are_explicitly_supported_by_ticket_text() -> None:
    for row in build_rows():
        signals = row["gold_impact_signals"]
        if isinstance(signals, str):
            signals = json.loads(signals)
        message = row["message"]
        if signals["affected_scope"] == "team":
            assert "整个团队" in message
        if signals["repeat_contacts"] >= 2:
            assert "联系客服两次" in message


def test_singletons_are_natural_and_textually_unique() -> None:
    singletons = [row for row in build_rows() if row["gold_issue_family"].startswith("SINGLETON-")]
    titles = [row["gold_issue_title"] for row in singletons]
    assert len(singletons) == 40
    assert len(set(titles)) == 40
    assert all("一次性问题" not in title for title in titles)
    assert all(title in row["message"] for title, row in zip(titles, singletons, strict=True))
