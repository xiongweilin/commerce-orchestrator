import csv
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from feedback_app.routing import derive_severity, needs_escalation, route_owner
from feedback_app.schemas import ImpactSignals, ProblemType, ProductArea

# Unicode literals used below — avoid f-string backslash escapes for Python 3.10 compat
_COMMA = "\uff0c"          # fullwidth comma
_PERIOD = "\u3002"         # CJK full stop
_TEAM_MESSAGE = "\u8be5\u95ee\u9898\u5f71\u54cd\u6574\u4e2a\u56e2\u961f"  # 该问题影响整个团队
_REPEAT_MESSAGE = "\u5df2\u7ecf\u8054\u7cfb\u5ba2\u670d\u4e24\u6b21"      # 已经联系客服两次


def support_counts(rows: list[dict]) -> dict:
    return {
        "problem_type_support": Counter(row["gold_problem_type"] for row in rows),
        "product_area_support": Counter(row["gold_product_area"] for row in rows),
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_holdout_csv(
    path: Path,
    rows: list[dict],
    *,
    audit_defaults: dict[str, str] | None = None,
    skip_if_reviewed: bool = False,
) -> None:
    """Write a holdout CSV, optionally skipping if already reviewed (candidate guard)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if skip_if_reviewed and path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            existing = list(csv.DictReader(handle))
        same_dataset = len(existing) == len(rows) and all(
            old.get("ticket_id") == new["ticket_id"] and old.get("message") == new["message"]
            for old, new in zip(existing, rows, strict=True)
        )
        reviewed = any(
            row.get("audit_label_text_consistent") or row.get("audit_notes") or row.get("auditor")
            for row in existing
        )
        if same_dataset and reviewed:
            return
    output = rows
    if audit_defaults is not None:
        output = [{**row, **audit_defaults} for row in rows]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)


def build_holdout_rows(
    families: list[tuple],
    *,
    start_date: datetime,
    ticket_prefix: str,
    split_label: str,
    blocked_cues: tuple[str, ...],
    team_mod: int = 3,
) -> list[dict]:
    """Shared holdout row builder."""
    rows: list[dict] = []
    start = start_date
    for family_index, (family, title, problem_type, product_area, first, second) in enumerate(
        families
    ):
        blocked = any(cue in f"{first}{second}" for cue in blocked_cues)
        for variant, base in enumerate((first, second)):
            team = variant == 1 and family_index % team_mod == 0
            repeat_contacts = 2 if variant == 1 else 0
            extras: list[str] = []
            if team:
                extras.append(_TEAM_MESSAGE)
            if repeat_contacts:
                extras.append(_REPEAT_MESSAGE)
            extras_text = _COMMA + _COMMA.join(extras) if extras else ""
            message = f"{base}{extras_text}{_PERIOD}"
            signals = {
                "affected_scope": "team" if team else "individual",
                "workflow_blocked": blocked,
                "data_loss_claimed": False,
                "repeat_contacts": repeat_contacts,
            }
            typed = ImpactSignals.model_validate(signals)
            severity = derive_severity(typed)
            number = family_index * 2 + variant + 1
            rows.append(
                {
                    "ticket_id": f"{ticket_prefix}-T{number:03d}",
                    "user_type": "enterprise_admin" if variant == 0 else "member",
                    "channel": "support_portal" if variant == 0 else "chat",
                    "message": message,
                    "created_at": (start + timedelta(hours=number * 3)).isoformat(),
                    "current_status": "open",
                    "gold_issue_family": family,
                    "gold_issue_title": title,
                    "gold_problem_type": problem_type,
                    "gold_product_area": product_area,
                    "gold_owner": route_owner(
                        ProblemType(problem_type), ProductArea(product_area)
                    ).value,
                    "gold_severity": severity.value,
                    "gold_escalation": str(needs_escalation(severity, typed)).lower(),
                    "gold_impact_signals": json.dumps(signals, ensure_ascii=False),
                    "split": split_label,
                }
            )
    return rows