from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytest.importorskip("portable_runtime")

from portable_runtime.public_contracts.catalog import contract_catalog

ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_MANIFEST = ROOT / "docs/contracts/responsibility-compatibility.toml"
BACKEND_DOCKERFILE = ROOT / "backend/Dockerfile"


def _compatibility() -> dict:
    with COMPATIBILITY_MANIFEST.open("rb") as handle:
        return tomllib.load(handle)


def _current_contract_ids(catalog: dict) -> set[str]:
    contracts = catalog.get("contracts", {})
    return {
        entry["current"]
        for entry in contracts.values()
        if isinstance(entry, dict) and isinstance(entry.get("current"), str)
    }


def test_installed_portable_runtime_matches_compatibility_manifest():
    compatibility = _compatibility()
    catalog = contract_catalog()

    assert catalog["catalog_version"] == compatibility["portable_contract_catalog"]
    assert catalog["owner"] == compatibility["portable_contract_owner"]

    required = set(compatibility["required_contracts"])
    missing = required - _current_contract_ids(catalog)
    assert not missing, f"portable-runtime is missing contracts: {sorted(missing)}"


def test_docker_builds_and_installs_pinned_portable_runtime_wheel():
    compatibility = _compatibility()
    revision = compatibility["portable_runtime_revision"]
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert f"ARG PORTABLE_RUNTIME_REV={revision}" in dockerfile
    assert "COPY --from=portable_runtime" in dockerfile
    assert "uv build --wheel --out-dir /wheels" in dockerfile
    assert "uv pip install --python /app/.venv/bin/python --no-deps /wheels/portable_runtime-*.whl" in dockerfile

    runtime_marker = "FROM python:3.12-slim AS runtime"
    assert runtime_marker in dockerfile
    runtime_stage = dockerfile.split(runtime_marker, maxsplit=1)[1]
    assert "/opt/reference-oracle" not in runtime_stage
