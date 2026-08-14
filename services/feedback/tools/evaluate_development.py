import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from tools import safe_path
from tools.evaluate import (
    cached_cluster_texts,
    clustering_evaluation,
    load_rows,
    structure_evaluation,
)


def main()-> None:  # noqa: S3776 (tool script - acceptable complexity)
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--analysis-cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    data_path = safe_path(args.data, must_exist=True)
    cache_path = safe_path(args.analysis_cache, must_exist=True)
    output_path = safe_path(args.out)
    rows = load_rows(data_path)
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    structure = structure_evaluation(
        rows,
        "dify",
        "feedback-routing-v3-candidate",
        analysis_cache=cache,
    )
    cluster_texts = cached_cluster_texts(rows, cache)
    groups = [
        f"{area}|{problem_type}"
        for area, problem_type in zip(
            structure["predicted_product_areas"],
            structure["predicted_problem_types"],
            strict=True,
        )
    ]
    clustering = clustering_evaluation(
        rows,
        rows,
        "bge",
        development_groups=groups,
        holdout_groups=groups,
        linkage="complete",
        development_cluster_texts=cluster_texts,
        holdout_cluster_texts=cluster_texts,
        raw_text_weight=0.2,
        block_by_problem_type=True,
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "state": "development_only_not_promotion_eligible",
        "boundary": (
            "v5 is a seen development set. Metrics are resubstitution evidence for "
            "candidate selection and must not be presented as holdout performance."
        ),
        "data": str(data_path),
        "analysis_cache": str(cache_path),
        "structure": structure,
        "clustering": clustering,
    }
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "development.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_path / "development.json")


if __name__ == "__main__":
    main()
