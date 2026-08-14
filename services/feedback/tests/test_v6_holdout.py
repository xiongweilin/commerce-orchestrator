import json
from collections import Counter, defaultdict

from tools.generate_v6_holdout import FAMILIES, build_rows


def test_v6_has_balanced_pairs_and_same_label_hard_negatives() -> None:
    rows = build_rows()
    families = Counter(row["gold_issue_family"] for row in rows)
    problem_types = Counter(row["gold_problem_type"] for row in rows)
    product_areas = Counter(row["gold_product_area"] for row in rows)
    combo_families: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        combo_families[(row["gold_problem_type"], row["gold_product_area"])].add(
            row["gold_issue_family"]
        )
    assert len(rows) == 60
    assert len(FAMILIES) == 30
    assert set(families.values()) == {2}
    assert len(problem_types) == 8
    assert len(product_areas) == 8
    assert min(problem_types.values()) >= 2
    assert min(product_areas.values()) >= 2
    assert set(len(value) for value in combo_families.values()) == {2}


def test_v6_impact_signals_are_explicit_in_text() -> None:
    for row in build_rows():
        signals = json.loads(row["gold_impact_signals"])
        if signals["affected_scope"] == "team":
            assert "影响整个团队" in row["message"]
        if signals["repeat_contacts"] >= 2:
            assert "联系客服两次" in row["message"]
