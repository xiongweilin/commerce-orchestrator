import sys
import tempfile
from pathlib import Path

import pytest

from tools import (
    capture_analysis_cache,
    evaluate,
    evaluate_development,
    evaluate_workflow_suite,
    safe_path,
)


def _outside_file(tmp_path: Path, name: str, content: str = "") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("entrypoint", "argv"),
    [
        (
            capture_analysis_cache.main,
            ["capture_analysis_cache", "--data", "{outside}", "--out", "artifacts/cache.json"],
        ),
        (evaluate.main, ["evaluate", "--data", "{outside}"]),
        (
            evaluate_development.main,
            [
                "evaluate_development",
                "--data",
                "{outside}",
                "--analysis-cache",
                "{outside}",
                "--out",
                "artifacts/development-test",
            ],
        ),
        (
            evaluate_workflow_suite.main,
            ["evaluate_workflow_suite", "--holdout", "{outside}"],
        ),
    ],
)
def test_cli_entrypoints_reject_paths_outside_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    entrypoint,
    argv: list[str],
) -> None:
    outside = _outside_file(tmp_path, "outside.csv")
    monkeypatch.setattr(sys, "argv", [item.format(outside=outside) for item in argv])

    with pytest.raises(ValueError, match="outside project root"):
        entrypoint()


def test_cache_write_rejects_path_outside_project(tmp_path: Path) -> None:
    target = tmp_path / "cache.json"

    with pytest.raises(ValueError, match="outside project root"):
        capture_analysis_cache.save_cache(target, {"items": {}})

    assert not target.exists()


def test_safe_path_rejects_symlink_escape(tmp_path: Path) -> None:
    artifacts = safe_path("artifacts")
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts) as temporary:
        link = Path(temporary) / "escape"
        link.symlink_to(tmp_path, target_is_directory=True)

        with pytest.raises(ValueError, match="outside project root"):
            safe_path(link / "payload.json")
