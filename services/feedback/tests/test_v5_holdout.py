import json
from collections import Counter

from tools.generate_v5_holdout import FAMILIES, build_rows


def test_v5_holdout_is_balanced_and_pair_traceable() -> None:
    rows = build_rows()
    families = Counter(row["gold_issue_family"] for row in rows)
    problem_types = Counter(row["gold_problem_type"] for row in rows)
    product_areas = Counter(row["gold_product_area"] for row in rows)
    assert len(rows) == 60
    assert len(FAMILIES) == 30
    assert set(families.values()) == {2}
    assert len(problem_types) == 8
    assert len(product_areas) == 8
    assert min(problem_types.values()) >= 6
    assert min(product_areas.values()) >= 6


def test_v5_impact_signals_are_explicit_in_text() -> None:
    for row in build_rows():
        signals = json.loads(row["gold_impact_signals"])
        if signals["affected_scope"] == "team":
            assert "影响整个团队" in row["message"]
        if signals["repeat_contacts"] >= 2:
            assert "联系客服两次" in row["message"]
