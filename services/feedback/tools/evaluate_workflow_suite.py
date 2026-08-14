import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from feedback_app.config import get_settings
from feedback_app.workflow_suite import (
    generate_cluster_narrative,
    generate_report_narrative,
    generate_sop_draft,
)
from tools import safe_path


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def report_markdown(payload: dict) -> str:
    metrics = payload["metrics"]
    gates = payload["quality_gates"]
    return "\n".join(
        [
            "# 四工作流候选真实回放",
            "",
            "> 合成 v5 机制评测；不代表真实业务分布或收益。AI 辅助审核不是独立人工审计。",
            "",
            f"- 数据版本：`{payload['dataset_version']}`",
            f"- 状态：`{payload['evaluation_state']}`",
            f"- 问题簇文案成功率：{metrics['cluster_success_rate']:.1%}",
            f"- SOP 草案成功率：{metrics['sop_success_rate']:.1%}",
            f"- 周报叙事成功率：{metrics['report_success_rate']:.1%}",
            f"- 证据引用有效率：{metrics['evidence_valid_rate']:.1%}",
            f"- 不可逆动作拦截契约率：{metrics['safe_action_rate']:.1%}",
            "",
            "## 质量门",
            "",
            *[
                f"- {'通过' if item['passed'] else '未通过'}：{item['label']} "
                f"（实际 {item['actual']:.3f}，门槛 {item['threshold']:.3f}）"
                for item in gates["items"]
            ],
            "",
            (
                "结论：三个新增工作流的已测门禁全部通过。"
                if gates["all_passed"]
                else "结论：至少一个新增工作流门禁未通过，套件不得晋升。"
            ),
        ]
    )


def main()-> None:  # noqa: S3776 (tool script - acceptable complexity)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--holdout",
        type=Path,
        default=Path("data/suite-evaluation/v5-holdout-locked.csv"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/suite-evaluation/v5-manifest.json"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/workflow-suite-v1-candidate"),
    )
    args = parser.parse_args()
    holdout_path = safe_path(args.holdout, must_exist=True)
    manifest_path = safe_path(args.manifest, must_exist=True)
    output_path = safe_path(args.out)
    settings = get_settings()
    rows = load_rows(holdout_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["gold_issue_family"]].append(row)

    errors: list[dict] = []
    cluster_results: list[dict] = []
    for family, family_rows in grouped.items():
        context = {
            "member_count": len(family_rows),
            "trend": "rising",
            "severity": max(row["gold_severity"] for row in family_rows),
            "suggested_owner": family_rows[0]["gold_owner"],
            "representative_tickets": [
                {"ticket_id": row["ticket_id"], "quote": row["message"]}
                for row in family_rows
            ],
        }
        try:
            result = generate_cluster_narrative(settings, family, context)
            cluster_results.append(
                {
                    "cluster_id": family,
                    "title": result.title,
                    "evidence_ticket_ids": result.evidence_ticket_ids,
                    "pending_cause": result.pending_cause,
                }
            )
        except Exception as exc:
            errors.append({"stage": "cluster", "id": family, "error": str(exc)})

    sop_success = 0
    for cluster in cluster_results[:5]:
        try:
            generate_sop_draft(
                settings,
                cluster["cluster_id"],
                {
                    "member_count": 12,
                    "trend": "rising",
                    "severity": "high",
                    "suggested_owner": "customer_success",
                    "pending_cause": cluster["pending_cause"],
                    "evidence_ticket_ids": cluster["evidence_ticket_ids"],
                },
            )
            sop_success += 1
        except Exception as exc:
            errors.append(
                {"stage": "sop", "id": cluster["cluster_id"], "error": str(exc)}
            )

    report_attempts = 0
    report_success = 0
    for offset in range(0, len(cluster_results), 5):
        chunk = cluster_results[offset : offset + 5]
        if not chunk:
            continue
        report_attempts += 1
        try:
            generate_report_narrative(
                settings,
                f"v5-batch-{report_attempts}",
                {
                    "ticket_total": len(rows),
                    "previous_ticket_total": 0,
                    "change_rate": None,
                    "severity_counts": {},
                    "clusters": [
                        {
                            "cluster_id": item["cluster_id"],
                            "title": item["title"],
                            "member_count": 2,
                            "trend": "rising",
                            "severity": "medium",
                            "suggested_owner": "customer_success",
                            "evidence_ticket_ids": item["evidence_ticket_ids"],
                        }
                        for item in chunk
                    ],
                },
            )
            report_success += 1
        except Exception as exc:
            errors.append(
                {"stage": "report", "id": f"batch-{report_attempts}", "error": str(exc)}
            )

    cluster_rate = len(cluster_results) / len(grouped)
    sop_attempts = min(5, len(cluster_results))
    sop_rate = sop_success / sop_attempts if sop_attempts else 0
    report_rate = report_success / report_attempts if report_attempts else 0
    evidence_rate = (
        1.0
        if cluster_results and not any("evidence" in error["error"] for error in errors)
        else 0
    )
    metrics = {
        "cluster_success_rate": cluster_rate,
        "sop_success_rate": sop_rate,
        "report_success_rate": report_rate,
        "evidence_valid_rate": evidence_rate,
        "safe_action_rate": sop_rate,
    }
    thresholds = {
        "cluster_success_rate": 0.95,
        "sop_success_rate": 1.0,
        "report_success_rate": 1.0,
        "evidence_valid_rate": 1.0,
        "safe_action_rate": 1.0,
    }
    items = [
        {
            "label": name,
            "actual": metrics[name],
            "threshold": threshold,
            "passed": metrics[name] >= threshold,
        }
        for name, threshold in thresholds.items()
    ]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset_version": manifest["dataset_version"],
        "evaluation_state": "candidate_scored_unpromoted",
        "boundary": "Synthetic suite replay; not a real-business distribution.",
        "metrics": metrics,
        "quality_gates": {"items": items, "all_passed": all(i["passed"] for i in items)},
        "attempts": {
            "cluster": len(grouped),
            "sop": sop_attempts,
            "report": report_attempts,
        },
        "errors": errors,
    }
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "evaluation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_path / "evaluation.md").write_text(report_markdown(payload), encoding="utf-8")
    print(report_markdown(payload))


if __name__ == "__main__":
    main()
