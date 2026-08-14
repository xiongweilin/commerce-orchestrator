from collections import Counter, defaultdict

from tools.generate_v6_holdout import FAMILIES as V6_FAMILIES
from tools.generate_v7_holdout import FAMILIES, build_rows


def test_v7_has_new_balanced_hard_negative_families() -> None:
    rows = build_rows()
    assert len(rows) == 60
    assert len(FAMILIES) == 30
    assert not ({item[0] for item in FAMILIES} & {item[0] for item in V6_FAMILIES})
    assert Counter(row["gold_problem_type"] for row in rows).keys() == {
        "integration",
        "bug",
        "data_consistency",
        "configuration",
        "permission",
        "performance",
        "how_to",
        "feature_request",
    }
    combo_families: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        combo_families[(row["gold_problem_type"], row["gold_product_area"])].add(
            row["gold_issue_family"]
        )
    assert all(len(families) == 2 for families in combo_families.values())
    assert all(count == 2 for count in Counter(row["gold_issue_family"] for row in rows).values())


def test_v7_high_impact_cues_remain_explicit() -> None:
    rows = build_rows()
    for row in rows:
        if row["gold_escalation"] == "true":
            assert "影响整个团队" in row["message"]
            assert "联系客服两次" in row["message"]
