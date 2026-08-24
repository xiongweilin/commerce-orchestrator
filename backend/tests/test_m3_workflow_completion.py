from __future__ import annotations

import re
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models.catalog import CatalogRevision, CatalogRevisionStatus
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.messaging import InboxEvent, OutboxEvent
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
    COMPLETION_RECHECK_EVENT,
    COMPLETION_RECHECK_TOPIC,
    COVERAGE_CLAIM,
    WORKFLOW_COMPLETION_CONTRACTS,
    assess_workflow_completion,
    completion_satisfied,
    request_completion_recheck,
)
from app.workflows.inbox_dispatch import plan_inbox_action


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


def _effect(
    db,
    run: WorkflowRun,
    effect_class: str = "odoo.bill_create",
    status: EffectStatus = EffectStatus.SUCCEEDED,
) -> EffectLedgerEntry:
    target_system, operation = effect_class.split(".", 1)
    effect = EffectLedgerEntry(
        intent_id=uuid.uuid4(),
        target_system=target_system,
        operation=operation,
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


def _required_effects(
    db,
    run: WorkflowRun,
    *,
    actor: uuid.UUID | None = None,
    omit: set[str] | None = None,
) -> dict[str, EffectLedgerEntry]:
    omitted = omit or set()
    rows: dict[str, EffectLedgerEntry] = {}
    contract = WORKFLOW_COMPLETION_CONTRACTS[run.workflow_type]
    for effect_class in contract.required_effect_classes_always:
        if effect_class in omitted:
            continue
        row = _effect(db, run, effect_class)
        rows[effect_class] = row
        if actor is not None:
            _verify_effect(db, row, actor)
    return rows


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
    assert WORKFLOW_COMPLETION_CONTRACTS["catalog-revision"].required_effect_classes_always == (
        "shopify.product_publish",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["listing-publication"].required_effect_classes_always == (
        "shopify.product_publish",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["procurement"].required_effect_classes_always == (
        "odoo.po_create",
        "odoo.po_confirm",
        "odoo.receive_transfer",
        "odoo.bill_create",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["return"].required_effect_classes_always == (
        "odoo.credit_note_create",
        "odoo.credit_note_validate",
        "shopify.refund_create",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["return-to-refund"].required_effect_classes_always == (
        "odoo.credit_note_create",
        "odoo.credit_note_validate",
        "shopify.refund_create",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["order-to-cash"].required_effect_classes_always == (
        "odoo.sale_order_create",
        "odoo.sale_order_confirm",
        "odoo.picking_create",
        "odoo.picking_validate",
        "shopify.fulfillment_create",
        "odoo.invoice_create",
        "odoo.invoice_validate",
    )
    assert WORKFLOW_COMPLETION_CONTRACTS["reconciliation"].required_effect_classes_always == ()


def test_effect_succeeded_does_not_complete_without_verified_realization(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run)
    for effect_class, effect in effects.items():
        if effect_class != "odoo.bill_create":
            _verify_effect(db, effect, actor)
    target = effects["odoo.bill_create"]
    assessment = assess_workflow_completion(db, run)
    assert f"effect:{target.intent_id}:realization_verified" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_human_approval_and_domain_closed_do_not_replace_required_effects(db) -> None:
    run, _ = _procurement_ready(db)
    assessment = assess_workflow_completion(db, run)
    assert "work_items:settled" in assessment.covered_requirements
    for effect_class in WORKFLOW_COMPLETION_CONTRACTS["procurement"].required_effect_classes_always:
        assert f"effect_class:{effect_class}:present" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_required_effect_class_missing_even_when_other_effects_are_verified(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    _required_effects(db, run, actor=actor, omit={"odoo.bill_create"})
    assessment = assess_workflow_completion(db, run)
    assert "effect_class:odoo.bill_create:present" in assessment.missing_requirements
    assert "effect_class:odoo.po_create:present" in assessment.covered_requirements
    assert assessment.realization_refs
    assert not completion_satisfied(assessment)


def test_resolved_reconciliation_diff_does_not_verify_realization(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run)
    for effect_class, effect in effects.items():
        if effect_class != "odoo.bill_create":
            _verify_effect(db, effect, actor)
    target = effects["odoo.bill_create"]
    _diff_for_effect(db, target, ReconciliationDiffStatus.RESOLVED)
    assessment = assess_workflow_completion(db, run)
    assert not assessment.blocking_reconciliation_refs
    assert f"effect:{target.intent_id}:realization_verified" in assessment.missing_requirements
    assert not completion_satisfied(assessment)


def test_resolution_note_is_not_realization_proof(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run)
    for effect_class, effect in effects.items():
        if effect_class != "odoo.bill_create":
            _verify_effect(db, effect, actor)
    target = effects["odoo.bill_create"]
    _diff_for_effect(
        db,
        target,
        ReconciliationDiffStatus.RESOLVED,
        resolution_note="operator says fixed",
    )
    assessment = assess_workflow_completion(db, run)
    assert f"effect:{target.intent_id}:realization_verified" in assessment.missing_requirements
    assert all(str(target.intent_id) not in ref for ref in assessment.realization_refs)


def test_open_reconciliation_diff_blocks_even_verified_effect(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run, actor=actor)
    target = effects["odoo.bill_create"]
    diff = _diff_for_effect(db, target, ReconciliationDiffStatus.OPEN)
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
    effect = _effect(db, run, "shopify.product_publish")
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
    effect = _effect(db, run, "shopify.product_publish")
    _verify_effect(db, effect, actor)
    assessment = assess_workflow_completion(db, run)
    assert completion_satisfied(assessment)
    assert assessment.qualification_refs
    assert assessment.realization_refs
    assert assessment.required_effect_classes == ["shopify.product_publish"]


def test_verified_realizations_and_closed_domain_cover_declared_procurement_scope(
    db, make_user
) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run, actor=actor)
    assessment = assess_workflow_completion(db, run)
    assert completion_satisfied(assessment)
    assert set(assessment.required_effect_refs) == {
        f"effect:{effect.intent_id}" for effect in effects.values()
    }
    assert assessment.required_effect_classes == list(
        WORKFLOW_COMPLETION_CONTRACTS["procurement"].required_effect_classes_always
    )


def test_blocked_run_has_real_durable_recheck_handoff(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    run.result_json = {
        "workflowId": str(run.id),
        "status": "running",
        "completionBlocked": True,
        "completionAssessment": {"missing_requirements": ["proof"]},
    }
    db.flush()

    result = request_completion_recheck(
        db,
        workflow_id=run.id,
        requested_by_user_id=actor,
    )
    event = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_id == uuid.UUID(result["recheckEventId"]))
    ).scalar_one()
    inbox = db.execute(
        select(InboxEvent).where(
            InboxEvent.event_id == event.event_id,
            InboxEvent.consumer == "worker",
        )
    ).scalar_one()
    action = plan_inbox_action(event)

    assert event.event_type == COMPLETION_RECHECK_EVENT
    assert event.payload["workflow_id"] == str(run.id)
    assert event.payload["requested_by_user_id"] == str(actor)
    assert inbox.consumer == "worker"
    assert action.kind == "send"
    assert action.destination_id == str(run.id)
    assert action.topic == COMPLETION_RECHECK_TOPIC
    assert action.idempotency_key == str(event.event_id)


def test_verified_realization_then_recheck_reassesses_same_run_as_satisfied(db, make_user) -> None:
    actor = make_user(["system_admin"])
    run, _ = _procurement_ready(db)
    effects = _required_effects(db, run)
    for effect_class, effect in effects.items():
        if effect_class != "odoo.bill_create":
            _verify_effect(db, effect, actor)
    target = effects["odoo.bill_create"]
    before = assess_workflow_completion(db, run)
    assert not completion_satisfied(before)
    run.result_json = {
        "workflowId": str(run.id),
        "status": "running",
        "completionBlocked": True,
        "completionAssessment": before.model_dump(mode="json"),
    }
    db.flush()

    _verify_effect(db, target, actor)
    recheck = request_completion_recheck(
        db,
        workflow_id=run.id,
        requested_by_user_id=actor,
    )
    event = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_id == uuid.UUID(recheck["recheckEventId"]))
    ).scalar_one()
    assert plan_inbox_action(event).topic == COMPLETION_RECHECK_TOPIC

    after = assess_workflow_completion(db, run)
    assert after.workflow_kind == before.workflow_kind
    assert completion_satisfied(after)
    assert f"effect:{target.intent_id}:realization_verified" in after.covered_requirements


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


def test_only_gated_completion_seam_can_produce_workflow_completed() -> None:
    producers: list[tuple[str, int]] = []
    pattern = re.compile(r"\b(?:run|workflow_run)\.status\s*=\s*WorkflowRunStatus\.COMPLETED\b")
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            producers.append((path.as_posix(), text.count("\n", 0, match.start()) + 1))

    assert len(producers) == 1, producers
    assert producers[0][0] == "app/workflows/definitions.py"

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


def test_blocked_completion_waits_for_recheck_instead_of_returning_running() -> None:
    source = Path("app/workflows/definitions.py").read_text(encoding="utf-8")
    drive_start = source.index("def _drive_v2")
    run_definition_start = source.index("def _run_definition")
    drive_body = source[drive_start:run_definition_start]
    assert "COMPLETION_RECHECK_TOPIC" in drive_body
    assert "DBOS.recv" in drive_body
    assert 'return _final_result(workflow_id, "running")' not in drive_body
    assert "future recovery/replay may re-run" not in source
    assert "v1 continuations reused by the v2 driver may mark the run completed" not in source
