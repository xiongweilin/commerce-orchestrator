import json

from tools import safe_path
from tools.evaluate import markdown_report, quality_gate_results


def main() -> None:
    path = safe_path("artifacts/evaluation/evaluation.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["quality_gates"] = quality_gate_results(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_path("artifacts/evaluation/evaluation.md").write_text(
        markdown_report(payload), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
