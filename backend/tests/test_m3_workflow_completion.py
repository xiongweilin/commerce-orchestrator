from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.reconciliation import (
    ReconciliationDiff,
    ReconciliationDiffStatus,
    ReconciliationRun,
    ReconciliationRunStatus,
)
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from app.services.publication_qualification import append_publication_qualification_assessment
from app.services.realization_resolution import append_effect_realization_assessment
from app.services.workflow_completion import (
    COVERAGE_CLAIM,
    WORKFLOW_COMPLETION_CONTRACTS,
    assess_workflow_completion,
    completion_satisfied,
)


def _run(db, kind: str, *, input_json=None, result_json=None) -> WorkflowRun:
    run = WorkflowRun(
        workflow_type=kind,
        workflow_version=2,
        orchestration_engine="dbos",
        status=WorkflowRunStatus.RUNNING,
        input_json=input_json or {},
        result_json=result_json,
    )
    db.add(run)
    db.flush()
    return run


def _item(db, run: WorkflowRun, payload: dict) -> WorkItem:
    item = WorkItem(
        workflow_id=run.id,
        kind=WorkItemKind.APPROVAL,
        title="settled M3 fixture",
        payload_json=payload,
        status=WorkItemStatus.APPROVED,
        expected_version=1,
    )
    db.add(item)
    db.flush()
    return item


def _procurement_ready(db) -> tuple[WorkflowRun, ProcurementOrder]:
    run = _run(db, "procurement")
    order = ProcurementOrder(
        sku="SKU-M3",
        qty=Decimal("1"),
        supplier="ACME",
        unit_cost=Decimal("10"),
        currency="CNY",
        status=ProcurementStatus.CLOSED,
    )
    db.add(order)
    db.flush()
    _item(db, run, {"po_id": str(order.id)})
    return run, order


def _effect(db, run: WorkflowRun, status: EffectStatus = EffectStatus.SUCCEEDED) -> EffectLedgerEntry:
    effect = EffectLedgerEntry(
        intent_id=uuid.uuid4(),
        target_system="odoo",
        operation="bill_create",
        approval_ref=run.id,
        status=status,
    )
    db.add(effect)
    db.flush()
    return effect


def _verify_effect(db, effect: EffectLedgerEntry, actor: uuid.UUID) -> None:
    append_effect_realization_assessment(
        db,
        effect_id=effect.id,
        realization_status="verified",
        assessed_by_user_id=actor,
        evidence_refs=["readback:m3"],
    )


def _diff_for_effect(
    db,
    effect: EffectLedgerEntry,
    status: ReconciliationDiffStatus,
    *,
    resolution_note: str | None = None,
) -> ReconciliationDiff:
    rec_run = ReconciliationRun(
        run_type="m3-test",
        status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS,
    )
    db.add(rec_run)
    db.flush()
    diff = ReconciliationDiff(
        run_id=rec_run.id,
        domain="effect",
        entity_type="effect",
        entity_id=str(effect.intent_id),
        status=status,
        resolution_note=resolution_note,
    )
    db.add(diff)
    db.flush()
    return diff


def test_contract_inventory_is_finite_and_explicit() -> None:
    assert set(WORKFLOW_COMPLETION_CONTRACTS) == {
        "catalog-revision",
        "listing-publication",
        "procurement",
        "return",
        "order-to-cash",
        "return-to-refund",
        "reconciliation",
    }
    assert all(contract.workflow_kind == kind for kind, contract in WORKFLOW_COMPLETION_CONTRACTS.items())


def test_effect_succeeded_does_not_complete_without_verified_realization(db) -> None:
    run, _ = _procurement_ready(db)
    effect = _effect(db, run, EffectStatus.SUCCEEDED)
    assessment = assess_workflow_completion(db, run)
    assert f"effect:{effect.intent_id}:realization_verified" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_human_approval_and_domain_closed_do_not_replace_required_effect(db) -> None:
    run, _ = _procurement_ready(db)
    assessment = assess_workflow_completion(db, run)
    assert "work_items:settled" in assessment.covered_requirements
    assert "effects:present" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_resolved_reconciliation_diff_does_not_verify_realization(db) -> None:
    run, _ = _procurement_ready(db)
    effect = _effect(db, run)
    _diff_for_effect(db, effect, ReconciliationDiffStatus.RESOLVED)
    assessment = assess_workflow_completion(db, run)
    assert not assessment.blocking_reconciliation_refs
    assert f"effect:{effect.intent_id}:realization_verified" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_resolution_note_is_not_realization_proof(db) -> None:
    run, _ = _procurement_ready(db)
    effect = _effect(db, run)
    _diff_for_effect(
        db,
        effect,
        ReconciliationDiffStatus.RESOLVED,
        resolution_note="operator says fixed",
    )
    assessment = assess_workflow_completion(db, run)
    assert not assessment.realization_refs
    assert f"effect:{effect.intent_id}:realization_verified" in assessment.missing_requirements


def test_open_reconciliation_diff_blocks_even_verified_effect(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effect = _effect(db, run)
    _verify_effect(db, effect, actor)
    diff = _diff_for_effect(db, effect, ReconciliationDiffStatus.OPEN)
    assessment = assess_workflow_completion(db, run)
    assert f"reconciliation_diff:{diff.id}" in assessment.blocking_reconciliation_refs
    assert "reconciliation:no_blocking_diff_for_required_effects" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_catalog_official_does_not_replace_current_qualification(db, make_user) -> None:
    actor = make_user(["system_admin"])
    context = {
        "purpose": "publish",
        "policy_version": "policy-v1",
        "adapter_version": "shopify-v1",
        "environment_ref": "prod",
    }
    run = _run(db, "catalog-revision", input_json={"qualification_context": context})
    revision = CatalogRevision(
        sku="SKU-CAT-M3",
        title="M3",
        status=CatalogRevisionStatus.OFFICIAL,
        proposed={"title": "M3"},
    )
    db.add(revision)
    db.flush()
    _item(db, run, {"revision_id": str(revision.id)})
    effect = _effect(db, run)
    _verify_effect(db, effect, actor)
    assessment = assess_workflow_completion(db, run)
    assert "domain_terminal:catalog_revision:official" in assessment.covered_requirements
    assert "publication_qualification:current" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_current_qualification_plus_verified_realization_can_cover_catalog_scope(
    db, make_user
) -> None:
    actor = make_user(["system_admin"])
    context = {
        "purpose": "publish",
        "policy_version": "policy-v1",
        "adapter_version": "shopify-v1",
        "environment_ref": "prod",
    }
    run = _run(db, "catalog-revision", input_json={"qualification_context": context})
    revision = CatalogRevision(
        sku="SKU-CAT-M3-PASS",
        title="M3",
        status=CatalogRevisionStatus.OFFICIAL,
        proposed={"title": "M3"},
    )
    db.add(revision)
    db.flush()
    _item(db, run, {"revision_id": str(revision.id)})
    append_publication_qualification_assessment(
        db,
        catalog_revision_id=revision.id,
        channel="shopify",
        purpose="publish",
        policy_version="policy-v1",
        adapter_version="shopify-v1",
        environment_ref="prod",
        assessment_status="qualified",
        evidence_refs=["qualification:m3"],
    )
    effect = _effect(db, run)
    _verify_effect(db, effect, actor)
    assessment = assess_workflow_completion(db, run)
    assert completion_satisfied(assessment)
    assert assessment.qualification_refs
    assert assessment.realization_refs


def test_verified_realization_and_closed_domain_can_complete_declared_procurement_scope(
    db, make_user
) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effect = _effect(db, run)
    _verify_effect(db, effect, actor)
    assessment = assess_workflow_completion(db, run)
    assert completion_satisfied(assessment)
    assert assessment.required_effect_refs == [f"effect:{effect.intent_id}"]
    assert assessment.required_effect_classes == ["odoo.bill_create"]


def test_unknown_undeclared_obligation_is_not_falsely_claimed_covered(db) -> None:
    rec_run = ReconciliationRun(run_type="m3-scope", status=ReconciliationRunStatus.COMPLETED)
    db.add(rec_run)
    db.flush()
    run = _run(
        db,
        "reconciliation",
        input_json={"unknown_future_obligation": "not-declared"},
        result_json={"reconciliationRunId": str(rec_run.id)},
    )
    assessment = assess_workflow_completion(db, run)
    assert completion_satisfied(assessment)
    assert assessment.coverage_claim == COVERAGE_CLAIM == "declared-scope-only"
    assert all("unknown_future_obligation" not in req for req in assessment.covered_requirements)


def test_workflow_driver_routes_completed_write_through_bounded_gate() -> None:
    source = Path("app/workflows/definitions.py").read_text(encoding="utf-8")
    complete_start = source.index("def _complete_txn")
    cancel_start = source.index("def _cancel_txn")
    complete_body = source[complete_start:cancel_start]
    assert "assess_workflow_completion" in complete_body
    assert "completion_satisfied" in complete_body
    assert complete_body.index("assess_workflow_completion") < complete_body.index(
        "run.status = WorkflowRunStatus.COMPLETED"
    )
    assert '"completionAssessment"' in complete_body
