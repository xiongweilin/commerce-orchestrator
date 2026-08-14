import csv
import hashlib
import json
from pathlib import Path

import pytest

from tools.evaluate import (
    analysis_input_hash,
    candidate_status_payload,
    load_and_verify_manifest,
    load_audit_status,
    structure_evaluation,
)


def test_worker_mounts_evaluation_inputs_read_only() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    api = compose.split("  feedback-api:", 1)[1].split("\n  feedback-worker:", 1)[0]
    worker = compose.split("  feedback-worker:", 1)[1].split("\n  feedback-web:", 1)[0]
    assert "./artifacts:/app/artifacts:ro" in api
    assert "./data:/app/data:ro" in worker
    assert "./dify-workflows:/app/dify-workflows:ro" in worker


def test_failed_candidate_gates_block_promotion() -> None:
    payload = {
        "candidate_prompt_sha256": "hash",
        "dataset_version": "v4",
        "structure": {"sample_count": 60},
        "audit": {"consistent": 60},
        "quality_gates": {
            "items": [
                {"label": "structure", "passed": True},
                {"label": "clustering", "passed": False},
            ]
        },
    }
    status = candidate_status_payload(payload)
    assert status["promotion_state"] == "blocked_quality_gates"
    assert status["failed_quality_gates"] == ["clustering"]


def test_candidate_manifest_rejects_prompt_changed_after_freeze(tmp_path) -> None:
    prompt = tmp_path / "candidate.yml"
    prompt.write_text("version: one\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate_prompt_path": str(prompt),
                "candidate_prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    assert load_and_verify_manifest(manifest)["candidate_prompt_path"] == str(prompt)
    prompt.write_text("version: two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after holdout freeze"):
        load_and_verify_manifest(manifest)


def test_manifest_rejects_any_changed_frozen_file(tmp_path) -> None:
    frozen = tmp_path / "clustering.py"
    frozen.write_text("version = 1\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "frozen_files": [
                    {
                        "path": str(frozen),
                        "sha256": hashlib.sha256(frozen.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    load_and_verify_manifest(manifest)
    frozen.write_text("version = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen file changed"):
        load_and_verify_manifest(manifest)


def test_audit_must_match_holdout_and_be_complete(tmp_path) -> None:
    audit = tmp_path / "audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["ticket_id", "message", "audit_label_text_consistent", "auditor"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ticket_id": "T1",
                "message": "message",
                "audit_label_text_consistent": "yes",
                "auditor": "AI-assisted",
            }
        )
    status = load_audit_status(audit, [{"ticket_id": "T1", "message": "message"}])
    assert status["consistent"] == 1
    with pytest.raises(ValueError, match="do not match"):
        load_audit_status(audit, [{"ticket_id": "T2", "message": "message"}])


def test_structure_evaluation_uses_only_hash_matched_cache() -> None:
    row = {
        "ticket_id": "T1",
        "user_type": "member",
        "channel": "chat",
        "message": "从 Jira 同步任务后负责人字段一直为空。",
        "created_at": "2026-07-01T00:00:00+00:00",
        "current_status": "open",
        "gold_problem_type": "integration",
        "gold_product_area": "member_permission",
        "gold_owner": "technical_support",
        "gold_escalation": "false",
    }
    cached = {
        "items": {
            "T1": {
                "input_sha256": analysis_input_hash(row),
                "attempts": 2,
                "attempt_errors": ["ConnectTimeout: timed out"],
                "analysis": {
                    "summary": "Jira 负责人同步为空",
                    "problem_type": "data_consistency",
                    "product_area": "open_api",
                    "sentiment": "confused",
                    "impact_signals": {},
                    "evidence_spans": [{"quote": "从 Jira 同步任务后负责人字段一直为空"}],
                },
            }
        }
    }
    result = structure_evaluation(
        [row],
        "dify",
        "feedback-routing-v3-candidate",
        analysis_cache=cached,
    )
    assert result["problem_type"]["matrix"][4][4] == 1
    assert result["product_area"]["matrix"][2][2] == 1
    assert result["owner_policy_consistency"] == 1
    assert result["schema_contract_valid_rate"] == 1
    assert result["first_attempt_dependency_success_rate"] == 0

    cached["items"]["T1"]["input_sha256"] = "wrong"
    mismatch = structure_evaluation(
        [row],
        "dify",
        "feedback-routing-v3-candidate",
        analysis_cache=cached,
    )
    assert "analysis_cache_input_mismatch" in mismatch["errors"][0]["error"]
