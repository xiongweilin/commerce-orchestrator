import json
from collections import Counter

from tools.generate_candidate_holdout import FAMILIES, build_rows, write_csv


def test_candidate_holdout_is_balanced_and_pair_traceable() -> None:
    rows = build_rows()
    families = Counter(row["gold_issue_family"] for row in rows)
    problem_types = Counter(row["gold_problem_type"] for row in rows)
    product_areas = Counter(row["gold_product_area"] for row in rows)
    assert len(rows) == 60
    assert len(FAMILIES) == 30
    assert set(families.values()) == {2}
    assert min(problem_types.values()) >= 6
    assert min(product_areas.values()) >= 4
    assert len({row["ticket_id"] for row in rows}) == 60


def test_candidate_holdout_impact_signals_are_explicit() -> None:
    for row in build_rows():
        signals = json.loads(row["gold_impact_signals"])
        message = row["message"]
        if signals["affected_scope"] == "team":
            assert "影响整个团队" in message
        if signals["repeat_contacts"] >= 2:
            assert "联系客服两次" in message


def test_regeneration_preserves_completed_candidate_audit(tmp_path) -> None:
    path = tmp_path / "audit.csv"
    rows = build_rows()
    write_csv(path, rows, include_audit=True)
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    lines[1] = f"{lines[1][:-2]}yes,reviewed,reviewer"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    before = path.read_bytes()
    write_csv(path, rows, include_audit=True)
    assert path.read_bytes() == before
