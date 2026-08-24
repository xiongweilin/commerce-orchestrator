from __future__ import annotations

import re
from pathlib import Path


def test_workflow_completed_has_one_gated_producer_in_app_tree() -> None:
    """No app code may terminalize a WorkflowRun outside the M3 seam."""
    producers: list[tuple[str, int]] = []
    pattern = re.compile(r"\bstatus\s*=\s*WorkflowRunStatus\.COMPLETED\b")

    for path in Path("app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            producers.append((path.as_posix(), source.count("\n", 0, match.start()) + 1))

    assert len(producers) == 1, producers
    assert producers[0][0] == "app/workflows/definitions.py"

    definitions = Path("app/workflows/definitions.py").read_text(encoding="utf-8")
    complete_start = definitions.index("def _complete_txn")
    cancel_start = definitions.index("def _cancel_txn")
    completion_seam = definitions[complete_start:cancel_start]
    assert "assess_workflow_completion" in completion_seam
    assert "completion_satisfied" in completion_seam
    assert completion_seam.index("completion_satisfied") < completion_seam.index(
        "run.status = WorkflowRunStatus.COMPLETED"
    )
