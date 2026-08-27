from __future__ import annotations

import tomllib
from pathlib import Path

from portable_runtime.public_contracts.catalog import contract_catalog

ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_MANIFEST = ROOT / "docs/contracts/responsibility-compatibility.toml"
BACKEND_PYPROJECT = ROOT / "backend/pyproject.toml"
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


def test_installed_reference_oracle_matches_compatibility_manifest():
    compatibility = _compatibility()
    catalog = contract_catalog()

    assert catalog["catalog_version"] == compatibility["portable_contract_catalog"]
    assert catalog["owner"] == compatibility["portable_contract_owner"]

    required = set(compatibility["required_contracts"])
    missing = required - _current_contract_ids(catalog)
    assert not missing, f"portable-runtime reference oracle is missing contracts: {sorted(missing)}"


def test_portable_runtime_is_not_a_commerce_runtime_dependency():
    with BACKEND_PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)

    dependencies = pyproject["project"]["dependencies"]
    assert all(not dependency.lower().startswith("portable-runtime") for dependency in dependencies)


def test_docker_builder_oracle_pin_matches_manifest_and_stays_out_of_runtime():
    compatibility = _compatibility()
    revision = compatibility["reference_revision"]
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert f"ARG PORTABLE_RUNTIME_ORACLE_REV={revision}" in dockerfile

    runtime_marker = "FROM python:3.12-slim AS runtime"
    assert runtime_marker in dockerfile
    runtime_stage = dockerfile.split(runtime_marker, maxsplit=1)[1]
    assert "/opt/reference-oracle" not in runtime_stage
