import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sklearn.metrics import classification_report, confusion_matrix

from feedback_app.analysis import finalize_analysis
from feedback_app.analyzers import DemoAnalyzer, DifyAnalyzer
from feedback_app.clustering import normalize_ticket_text, select_threshold, threshold_clusters
from feedback_app.config import get_settings
from feedback_app.embeddings import (
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    blended_embeddings,
)
from feedback_app.schemas import LLMAnalysis, ProblemType, ProductArea, TicketInput
from tools import safe_path


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def analysis_input_hash(row: dict) -> str:
    canonical = json.dumps(
        {key: row[key] for key in ("ticket_id", "user_type", "channel", "message", "created_at")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def cached_cluster_texts(rows: list[dict], cache: dict) -> list[str]:
    cluster_texts: list[str] = []
    for row in rows:
        item = cache.get("items", {}).get(row["ticket_id"])
        if not item or "analysis" not in item:
            raise ValueError(f"analysis cache missing success for {row['ticket_id']}")
        if item.get("input_sha256") != analysis_input_hash(row):
            raise ValueError(f"analysis cache input mismatch for {row['ticket_id']}")
        analysis = LLMAnalysis.model_validate(item["analysis"])
        cluster_texts.append(analysis.issue_signature or analysis.summary)
    return cluster_texts


def cached_routing_groups(
    rows: list[dict],
    cache: dict,
    routing_policy_version: str,
    block_by_problem_type: bool,
) -> list[str]:
    groups: list[str] = []
    for row in rows:
        item = cache.get("items", {}).get(row["ticket_id"])
        if not item or "analysis" not in item:
            raise ValueError(f"analysis cache missing success for {row['ticket_id']}")
        raw = LLMAnalysis.model_validate(item["analysis"])
        final = finalize_analysis(
            row["message"],
            raw,
            get_settings().workflow_version,
            "cache",
            routing_policy_version,
        )
        group = final.product_area.value
        if block_by_problem_type:
            group = f"{group}|{final.problem_type.value}"
        groups.append(group)
    return groups


def load_audit_status(path: Path, expected_rows: list[dict]) -> dict:
    rows = load_rows(path)
    expected = {(row["ticket_id"], row["message"]) for row in expected_rows}
    audited = {(row["ticket_id"], row["message"]) for row in rows}
    if audited != expected:
        raise ValueError("audit rows do not match the selected holdout")
    labels = Counter(row.get("audit_label_text_consistent", "").strip().lower() for row in rows)
    auditors = sorted(
        {
            row.get("auditor", "").strip()
            for row in rows
            if row.get("auditor", "").strip()
        }
    )
    status = {
        "review_type": "AI-assisted candidate consistency review",
        "independent_human_audit": False,
        "row_count": len(rows),
        "consistent": labels["yes"],
        "inconsistent": labels["no"],
        "unreviewed": len(rows) - labels["yes"] - labels["no"],
        "auditors": auditors,
        "boundary": (
            "Checks synthetic text/label consistency and deterministic policy derivation; "
            "does not establish real-business validity or replace independent human review."
        ),
    }
    if status["unreviewed"]:
        raise ValueError(f"audit has {status['unreviewed']} unreviewed rows")
    return status

def _verify_manifest_single(manifest: dict, path_key: str, hash_key: str, error_msg: str) -> None:
    """Verify a single path/hash pair from a manifest."""
    file_path = manifest.get(path_key)
    expected = manifest.get(hash_key)
    if file_path and expected:
        actual = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()  # NOSONAR
        if actual != expected:
            raise ValueError(error_msg)


def _verify_manifest_files(manifest: dict, key: str, error_template: str) -> None:
    """Verify all files listed under *key* in manifest against their stored hashes."""
    for entry in manifest.get(key, []):
        file_path = Path(entry["path"])  # NOSONAR
        actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(error_template.format(path=file_path))



def load_and_verify_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    _verify_manifest_single(manifest, "candidate_prompt_path", "candidate_prompt_sha256",
                            "candidate prompt changed after holdout freeze; create a new holdout version")  # noqa: E501
    _verify_manifest_files(manifest, "frozen_files", "frozen file changed after holdout freeze: {path}; create a new holdout")  # noqa: E501
    _verify_manifest_files(manifest, "development_files", "development file changed after freeze: {path}")  # noqa: E501
    _verify_manifest_single(manifest, "development_dataset", "development_sha256", "development dataset changed after holdout freeze")  # noqa: E501
    for path_key, hash_key in (
        ("holdout_path", "holdout_sha256"),
        ("audit_path", "audit_sha256"),
    ):
        if manifest.get(path_key) and manifest.get(hash_key):
            actual_hash = hashlib.sha256(safe_path(manifest[path_key]).read_bytes()).hexdigest()
            if actual_hash != manifest[hash_key]:
                raise ValueError(f"{path_key.removesuffix('_path')} changed after freeze")
    return manifest


def as_ticket(row: dict) -> TicketInput:
    return TicketInput(
        ticket_id=row["ticket_id"],
        user_type=row["user_type"],
        channel=row["channel"],
        message=row["message"],
        created_at=row["created_at"],
        current_status=row["current_status"],
    )


def confusion_payload(gold: list[str], predicted: list[str], labels: list[str]) -> dict:
    return {
        "labels": labels,
        "matrix": confusion_matrix(gold, predicted, labels=labels).tolist(),
        "report": classification_report(
            gold,
            predicted,
            labels=labels,
            output_dict=True,
            zero_division=0,
        ),
    }


def structure_evaluation(
    rows: list[dict],
    analyzer_name: str,
    routing_policy_version: str,
    analysis_cache: dict | None = None,
) -> dict:
    settings = get_settings()
    analyzer = DifyAnalyzer(settings) if analyzer_name == "dify" else DemoAnalyzer()
    predicted_types: list[str] = []
    predicted_areas: list[str] = []
    predicted_owners: list[str] = []
    predicted_escalations: list[bool] = []
    evidence_located = 0
    schema_contract_valid = 0
    first_attempt_dependency_success = 0
    errors: list[dict] = []
    for row in rows:
        try:
            if analysis_cache is None:
                raw = analyzer.analyze(as_ticket(row))
                attempts = 1
            else:
                cached = analysis_cache.get("items", {}).get(row["ticket_id"])
                if not cached or "analysis" not in cached:
                    raise ValueError("analysis_cache_missing_success")
                if cached.get("input_sha256") != analysis_input_hash(row):
                    raise ValueError("analysis_cache_input_mismatch")
                raw = LLMAnalysis.model_validate(cached["analysis"])
                attempts = int(cached.get("attempts", 1))
            schema_contract_valid += 1
            first_attempt_dependency_success += int(attempts == 1)
            final = finalize_analysis(
                row["message"],
                raw,
                settings.workflow_version,
                analyzer_name,
                routing_policy_version,
            )
            predicted_types.append(final.problem_type.value)
            predicted_areas.append(final.product_area.value)
            predicted_owners.append(final.suggested_owner.value)
            predicted_escalations.append(final.needs_escalation)
            evidence_located += int(bool(final.evidence_spans) and not final.review_reasons)
        except Exception as exc:
            errors.append({"ticket_id": row["ticket_id"], "error": f"{type(exc).__name__}: {exc}"})
            predicted_types.append("error")
            predicted_areas.append("error")
            predicted_owners.append("error")
            predicted_escalations.append(True)

    gold_types = [row["gold_problem_type"] for row in rows]
    gold_areas = [row["gold_product_area"] for row in rows]
    gold_owners = [row["gold_owner"] for row in rows]
    gold_escalations = [row["gold_escalation"] == "true" for row in rows]
    high_indexes = [index for index, expected in enumerate(gold_escalations) if expected]
    escalation_recall = (
        sum(predicted_escalations[index] for index in high_indexes) / len(high_indexes)
        if high_indexes
        else 1.0
    )
    return {
        "analyzer": analyzer_name,
        "routing_policy_version": routing_policy_version,
        "sample_count": len(rows),
        "schema_contract_valid_rate": schema_contract_valid / len(rows),
        "first_attempt_dependency_success_rate": (
            first_attempt_dependency_success / len(rows)
        ),
        # Backward-compatible alias. This now measures validated contract output,
        # while transport reliability is reported separately above.
        "first_pass_schema_rate": schema_contract_valid / len(rows),
        "evidence_auto_location_rate": evidence_located / len(rows),
        "problem_type": confusion_payload(
            gold_types, predicted_types, [item.value for item in ProblemType]
        ),
        "product_area": confusion_payload(
            gold_areas, predicted_areas, [item.value for item in ProductArea]
        ),
        "predicted_product_areas": predicted_areas,
        "predicted_problem_types": predicted_types,
        "owner_policy_consistency": sum(
            actual == expected
            for actual, expected in zip(predicted_owners, gold_owners, strict=True)
        )
        / len(rows),
        "escalation_recall": escalation_recall,
        "errors": errors,
    }


def clustering_evaluation(
    development: list[dict],
    holdout: list[dict],
    embedding_backend: str,
    development_groups: list[str] | None = None,
    holdout_groups: list[str] | None = None,
    linkage: str = "single",
    development_cluster_texts: list[str] | None = None,
    holdout_cluster_texts: list[str] | None = None,
    raw_text_weight: float = 1.0,
    block_by_problem_type: bool = False,
    min_pairwise_precision: float = 0.80,
) -> dict:
    all_rows = development + holdout
    texts = [normalize_ticket_text(row["message"]) for row in all_rows]
    if embedding_backend == "bge":
        embedder = SentenceTransformerEmbedder(get_settings().embedding_model)
    else:
        embedder = TfidfEmbedder()
    summaries = (development_cluster_texts or texts[: len(development)]) + (
        holdout_cluster_texts or texts[len(development) :]
    )
    vectors = blended_embeddings(embedder, texts, summaries, raw_text_weight)
    development_vectors = vectors[: len(development)]
    holdout_vectors = vectors[len(development) :]
    development_gold = [row["gold_issue_family"] for row in development]
    holdout_gold = [row["gold_issue_family"] for row in holdout]
    development_groups = development_groups or [
        (
            f"{row['gold_product_area']}|{row['gold_problem_type']}"
            if block_by_problem_type
            else row["gold_product_area"]
        )
        for row in development
    ]
    holdout_groups = holdout_groups or [row["gold_product_area"] for row in holdout]
    tuned = select_threshold(
        development_vectors,
        development_gold,
        groups=development_groups,
        linkage=linkage,
        minimum_pairwise_precision=min_pairwise_precision,
        thresholds=(
            [round(value / 100, 2) for value in range(30, 91)]
            if linkage == "complete"
            else None
        ),
    )
    holdout_labels = threshold_clusters(
        holdout_vectors,
        tuned.threshold,
        groups=holdout_groups,
        linkage=linkage,
    )

    false_merges: list[dict] = []
    for left in range(len(holdout)):
        for right in range(left + 1, len(holdout)):
            same_predicted = holdout_labels[left] == holdout_labels[right]
            different_gold = holdout_gold[left] != holdout_gold[right]
            if same_predicted and different_gold:
                false_merges.append(
                    {
                        "left": holdout[left]["ticket_id"],
                        "right": holdout[right]["ticket_id"],
                        "left_family": holdout_gold[left],
                        "right_family": holdout_gold[right],
                    }
                )
    family_clusters: dict[str, set[int]] = defaultdict(set)
    for family, label in zip(holdout_gold, holdout_labels, strict=True):
        family_clusters[family].add(label)
    false_splits = [
        {"family": family, "predicted_cluster_count": len(labels)}
        for family, labels in family_clusters.items()
        if len(labels) > 1 and not family.startswith("SINGLETON")
    ]
    holdout_metrics = select_threshold(
        holdout_vectors,
        holdout_gold,
        thresholds=[tuned.threshold],
        minimum_pairwise_precision=0,
        groups=holdout_groups,
        linkage=linkage,
    )
    return {
        "embedding_backend": embedding_backend,
        "embedding_input": "normalized_ticket_text_plus_issue_signature",
        "blocking_key": (
            "product_area+problem_type" if block_by_problem_type else "product_area"
        ),
        "linkage": linkage,
        "raw_text_weight": raw_text_weight,
        "issue_signature_weight": 1 - raw_text_weight,
        "development_count": len(development),
        "holdout_count": len(holdout),
        "frozen_threshold": tuned.threshold,
        "development_metrics": {
            "pairwise": tuned.pairwise,
            "b_cubed": tuned.b_cubed,
            "purity": tuned.purity,
        },
        "holdout_metrics": {
            "pairwise": holdout_metrics.pairwise,
            "b_cubed": holdout_metrics.b_cubed,
            "purity": holdout_metrics.purity,
        },
        "false_merge_examples": false_merges[:10],
        "false_split_examples": false_splits[:10],
    }


def markdown_report(payload: dict) -> str:
    structure = payload["structure"]
    clustering = payload["clustering"]
    problem_macro = structure["problem_type"]["report"][MACRO_KEY]["f1-score"]
    area_macro = structure["product_area"]["report"][MACRO_KEY]["f1-score"]
    holdout = clustering["holdout_metrics"]
    audit = payload["audit"]
    gates = payload["quality_gates"]
    return "\n".join(
        [
            "# 合成机制评测报告",
            "",
            (
                "> 锁定合成校验集 N=60，仅用于机制质量评估，不代表真实业务分布。"
                "当前已完成 AI 辅助一致性复核，不构成独立人工审计。"
            ),
            "",
            f"- 数据版本：`{payload['dataset_version']}`",
            f"- 评测状态：`{payload['evaluation_state']}`",
            *(
                [f"- 候选提示词 SHA-256：`{payload['candidate_prompt_sha256']}`"]
                if payload.get("candidate_prompt_sha256")
                else []
            ),
            (
                f"- AI 辅助复核：{audit['consistent']}/{audit['row_count']} 一致，"
                f"{audit['unreviewed']} 条未复核"
            ),
            f"- 分析器：`{structure['analyzer']}`",
            f"- Schema 契约有效率：{structure['schema_contract_valid_rate']:.1%}",
            (
                "- 外部依赖首轮成功率："
                f"{structure['first_attempt_dependency_success_rate']:.1%}"
                "（与 Schema 质量分开计量）"
            ),
            f"- 证据自动定位成功率：{structure['evidence_auto_location_rate']:.1%}",
            f"- 问题类型 Macro-F1：{problem_macro:.3f}",
            f"- 产品区域 Macro-F1：{area_macro:.3f}",
            f"- 责任路由策略一致率：{structure['owner_policy_consistency']:.1%}",
            f"- 高风险升级召回率：{structure['escalation_recall']:.1%}",
            f"- 重复问题匹配精确率：{holdout['pairwise']['precision']:.1%}",
            f"- 重复问题匹配召回率：{holdout['pairwise']['recall']:.1%}",
            f"- 聚类纯度：{holdout['purity']:.1%}",
            f"- B³ F1（技术指标）：{holdout['b_cubed']['f1']:.3f}",
            "",
            "## 质量门",
            "",
            *[
                f"- {'通过' if gate['passed'] else '未通过'}：{gate['label']} "
                f"（实际 {gate['actual']:.3f}，门槛 {gate['threshold']:.3f}）"
                for gate in gates["items"]
            ],
            "",
            (
                "结论：已测关键门全部通过。"
                if gates["all_measured_passed"]
                else "结论：存在未通过的关键质量门，只能声明机制已实现，不能声明整体质量达标。"
            ),
            "",
            "混淆矩阵、错误合并和错误拆分案例见同目录 JSON。",
        ]
    )


MACRO_KEY = "macro avg"

def quality_gate_results(payload: dict) -> dict:
    structure = payload["structure"]
    holdout = payload["clustering"]["holdout_metrics"]
    values = [
        ("Schema 契约有效率", structure["schema_contract_valid_rate"], 0.95),
        (
            "问题类型 Macro-F1",
            structure["problem_type"]["report"][MACRO_KEY]["f1-score"],
            0.80,
        ),
        (
            "产品区域 Macro-F1",
            structure["product_area"]["report"][MACRO_KEY]["f1-score"],
            0.80,
        ),
        ("责任路由策略一致率", structure["owner_policy_consistency"], 0.85),
        ("高风险升级召回率", structure["escalation_recall"], 1.0),
        ("quote 自动定位成功率", structure["evidence_auto_location_rate"], 0.95),
        ("重复问题匹配精确率", holdout["pairwise"]["precision"], 0.80),
        ("重复问题匹配召回率", holdout["pairwise"]["recall"], 0.50),
        ("B³ F1", holdout["b_cubed"]["f1"], 0.75),
    ]
    items = [
        {"label": label, "actual": actual, "threshold": threshold, "passed": actual >= threshold}
        for label, actual, threshold in values
    ]
    return {
        "items": items,
        "all_measured_passed": all(item["passed"] for item in items),
        "unmeasured": ["已接受记录 offset 有效率", "周报与 SOP 引用率"],
    }


def candidate_status_payload(payload: dict) -> dict:
    failed = [
        item["label"] for item in payload["quality_gates"]["items"] if not item["passed"]
    ]
    return {
        "workflow_state": "candidate_scored_unpromoted",
        "workflow_name": "客户反馈结构化-v3-candidate",
        "dsl_path": "dify-workflows/feedback-structuring-v3-candidate.yml",
        "candidate_prompt_sha256": payload.get("candidate_prompt_sha256"),
        "dataset_version": payload["dataset_version"],
        "holdout_rows": payload["structure"]["sample_count"],
        "audit_consistent": payload["audit"]["consistent"],
        "audit_type": "AI-assisted consistency review; not an independent human audit",
        "model_evaluation": "completed",
        "promotion_state": (
            "eligible_for_manual_promotion"
            if not failed
            else "blocked_quality_gates"
        ),
        "quality_gates_all_passed": not failed,
        "failed_quality_gates": failed,
        "boundary": (
            "A scored candidate remains separate from the official baseline. "
            "Promotion requires every measured gate to pass and an explicit promotion record."
        ),
    }


def main() -> None:  # noqa: S3776 (comprehensive evaluation tool)
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--holdout", type=Path)
    parser.add_argument("--development", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("data/manual-audit/holdout-audit.csv"),
    )
    parser.add_argument("--analyzer", choices=["demo", "dify"], default="demo")
    parser.add_argument("--embedding", choices=["tfidf", "bge"], default="tfidf")
    parser.add_argument("--linkage", choices=["single", "complete"], default="single")
    parser.add_argument(
        "--routing-policy-version",
        default="feedback-routing-v1",
    )
    parser.add_argument("--analysis-cache", type=Path)
    parser.add_argument("--development-analysis-cache", type=Path)
    parser.add_argument("--cluster-raw-text-weight", type=float, default=1.0)
    parser.add_argument("--cluster-block-by-problem-type", action="store_true")
    parser.add_argument(
        "--min-pairwise-precision",
        type=float,
        default=0.80,
        help="Minimum pairwise precision for development-set threshold selection.",
    )
    args = parser.parse_args()
    data_path = safe_path(args.data, must_exist=True)
    development_path = safe_path(
        args.development or data_path / "tickets_development_with_gold.csv",
        must_exist=True,
    )
    holdout_path = safe_path(
        args.holdout or data_path / "tickets_holdout_locked.csv",
        must_exist=True,
    )
    manifest_path = safe_path(
        args.manifest or data_path / "dataset_manifest.json",
        must_exist=True,
    )
    audit_path = safe_path(args.audit, must_exist=True)
    output_path = safe_path(args.out)
    analysis_cache_path = (
        safe_path(args.analysis_cache, must_exist=True) if args.analysis_cache else None
    )
    development_analysis_cache_path = (
        safe_path(args.development_analysis_cache, must_exist=True)
        if args.development_analysis_cache
        else None
    )
    development = load_rows(development_path)
    holdout = load_rows(holdout_path)
    manifest = load_and_verify_manifest(manifest_path)
    is_candidate = str(manifest.get("state", "")).startswith("candidate_")
    analysis_cache = (
        json.loads(analysis_cache_path.read_text(encoding="utf-8"))
        if analysis_cache_path
        else None
    )
    development_analysis_cache = (
        json.loads(development_analysis_cache_path.read_text(encoding="utf-8"))
        if development_analysis_cache_path
        else None
    )
    structure = structure_evaluation(
        holdout,
        args.analyzer,
        args.routing_policy_version,
        analysis_cache=analysis_cache,
    )
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "boundary": "Synthetic mechanism benchmark; not a real-business distribution.",
        "dataset_version": manifest["dataset_version"],
        "evaluation_state": "candidate_scored_unpromoted" if is_candidate else "official_baseline",
        "candidate_prompt_sha256": manifest.get("candidate_prompt_sha256"),
        "audit": load_audit_status(audit_path, holdout),
        "structure": structure,
        "clustering": clustering_evaluation(
            development,
            holdout,
            args.embedding,
            development_groups=(
                cached_routing_groups(
                    development,
                    development_analysis_cache,
                    args.routing_policy_version,
                    args.cluster_block_by_problem_type,
                )
                if development_analysis_cache
                else None
            ),
            holdout_groups=[
                (
                    f"{area}|{problem_type}"
                    if args.cluster_block_by_problem_type
                    else area
                )
                for area, problem_type in zip(
                    structure["predicted_product_areas"],
                    structure["predicted_problem_types"],
                    strict=True,
                )
            ],
            linkage=args.linkage,
            min_pairwise_precision=args.min_pairwise_precision,
            development_cluster_texts=(
                cached_cluster_texts(development, development_analysis_cache)
                if development_analysis_cache
                else None
            ),
            holdout_cluster_texts=(
                cached_cluster_texts(holdout, analysis_cache) if analysis_cache else None
            ),
            raw_text_weight=args.cluster_raw_text_weight,
            block_by_problem_type=args.cluster_block_by_problem_type,
        ),
        "holdout_support": {
            "problem_type": Counter(row["gold_problem_type"] for row in holdout),
            "product_area": Counter(row["gold_product_area"] for row in holdout),
        },
    }
    payload["quality_gates"] = quality_gate_results(payload)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "evaluation.json").write_text(  # NOSONAR -- output_path is project-bound
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_path / "evaluation.md").write_text(  # NOSONAR -- output_path is project-bound
        markdown_report(payload), encoding="utf-8"
    )
    if is_candidate:
        (output_path / "status.json").write_text(  # NOSONAR -- output_path is project-bound
            json.dumps(candidate_status_payload(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(markdown_report(payload))


if __name__ == "__main__":
    main()
