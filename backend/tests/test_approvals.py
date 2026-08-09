"""Approval work items: roles, four-eyes, versions, expiry, compliance veto."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
    VersionConflictError,
)
from app.core.time import utc_now
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemStatus,
)
from app.services.approvals import create_work_item, submit_decision
from app.services.commands import dispatch_command


def _work_items(db, run_id: uuid.UUID) -> list[WorkItem]:
    return list(
        db.execute(
            select(WorkItem).where(WorkItem.workflow_id == run_id).order_by(WorkItem.created_at)
        )
        .scalars()
        .all()
    )


def _dispatch_procurement(db, actor_user_id: uuid.UUID) -> tuple[uuid.UUID, WorkItem]:
    result = dispatch_command(
        db,
        scope=f"approval-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="procurement",
        payload={
            "sku": "SKU-PO-1",
            "qty": "10",
            "uom": "unit",
            "supplier": "ACME Supplies",
            "unit_cost": "5.00",
            "currency": "CNY",
        },
        actor_user_id=actor_user_id,
    )
    run_id = uuid.UUID(result["workflowId"])
    items = _work_items(db, run_id)
    assert len(items) == 1
    return run_id, items[0]


def test_approve_by_required_role_runs_continuation(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    run_id, item = _dispatch_procurement(db, proposer)
    assert item.required_roles == ["budget_owner"]
    assert (item.payload_json or {}).get("four_eyes_area") == "po"

    outcome = submit_decision(
        db,
        work_item_id=item.id,
        user_id=budget_owner,
        decision="approve",
        reason="within budget",
        expected_workflow_version=item.expected_version,
    )
    assert outcome["status"] == "approved"
    assert outcome["workflowStatus"] == "awaiting_approval"

    db.flush()
    items = _work_items(db, run_id)
    assert len(items) == 2
    assert items[1].kind.value == "confirmation"
    assert items[1].required_roles == ["warehouse_staff"]
    assert items[1].status == WorkItemStatus.PENDING

    from app.models.effect import EffectLedgerEntry

    effects = db.execute(select(EffectLedgerEntry)).scalars().all()
    assert {f"{e.target_system}.{e.operation}" for e in effects} == {
        "odoo.po_create",
        "odoo.po_confirm",
    }


def test_full_procurement_approval_chain_completes_run(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    warehouse = make_user(["warehouse_staff"])
    accountant_a = make_user(["accountant"])
    accountant_b = make_user(["accountant"])

    run_id, item = _dispatch_procurement(db, proposer)
    steps = [
        (item, budget_owner, "approve"),
        (None, warehouse, "confirm"),
        (None, accountant_a, "approve"),
        (None, accountant_b, "approve"),
    ]
    for idx, (work_item, user, decision) in enumerate(steps):
        if work_item is None:
            work_item = _work_items(db, run_id)[-1]
        outcome = submit_decision(
            db,
            work_item_id=work_item.id,
            user_id=user,
            decision=decision,
            reason=f"step {idx}",
            expected_workflow_version=work_item.expected_version,
        )
        db.flush()
        assert outcome["workflowId"] == str(run_id)

    run = db.get(WorkflowRun, run_id)
    assert run.status == WorkflowRunStatus.COMPLETED

    from app.models.procurement import ProcurementOrder

    order = db.execute(select(ProcurementOrder)).scalars().one()
    assert order.status.value == "closed"
    # Every gate decision was audited.
    decisions = db.execute(select(func.count()).select_from(WorkItemDecision)).scalar_one()
    assert decisions == 4


def test_wrong_role_rejected(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    warehouse = make_user(["warehouse_staff"])
    _, item = _dispatch_procurement(db, proposer)
    with pytest.raises(PermissionDeniedError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=warehouse,
            decision="approve",
            expected_workflow_version=item.expected_version,
        )


def test_four_eyes_proposer_cannot_approve_own_po(db, make_user) -> None:
    proposer = make_user(["procurement_lead", "budget_owner"])
    _, item = _dispatch_procurement(db, proposer)
    with pytest.raises(PermissionDeniedError, match="four-eyes"):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=proposer,
            decision="approve",
            expected_workflow_version=item.expected_version,
        )


def test_expected_workflow_version_mismatch(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    _, item = _dispatch_procurement(db, proposer)
    with pytest.raises(VersionConflictError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=budget_owner,
            decision="approve",
            expected_workflow_version=999,
        )


def test_missing_expected_version_rejected(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    _, item = _dispatch_procurement(db, proposer)
    with pytest.raises(ValidationError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=budget_owner,
            decision="approve",
        )


def test_expired_work_item_rejected(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    run_id, _ = _dispatch_procurement(db, proposer)
    expired = create_work_item(
        db,
        workflow_id=run_id,
        kind="approval",
        title="Expired task",
        required_roles=["budget_owner"],
        expires_at=utc_now() - timedelta(minutes=1),
    )
    budget_owner = make_user(["budget_owner"])
    with pytest.raises(ConflictError, match="expired"):
        submit_decision(
            db,
            work_item_id=expired.id,
            user_id=budget_owner,
            decision="approve",
            expected_workflow_version=expired.expected_version,
        )


def test_compliance_can_veto_but_not_approve(db, make_user) -> None:
    proposer = make_user(["catalog_owner"])
    compliance = make_user(["compliance"])

    result = dispatch_command(
        db,
        scope=f"veto-{uuid.uuid4()}",
        key=f"key-{uuid.uuid4()}",
        command_type="catalog-revision",
        payload={"sku": "SKU-VETO", "proposed": {"title": "x"}},
        actor_user_id=proposer,
    )
    run_id = uuid.UUID(result["workflowId"])
    item = _work_items(db, run_id)[0]
    assert (item.payload_json or {}).get("compliance_vetoable") is True

    # Compliance may reject (veto), which cancels the run.
    outcome = submit_decision(
        db,
        work_item_id=item.id,
        user_id=compliance,
        decision="reject",
        reason="compliance veto",
        expected_workflow_version=item.expected_version,
    )
    assert outcome["workflowStatus"] == "cancelled"
    assert db.get(WorkflowRun, run_id).status == WorkflowRunStatus.CANCELLED


def test_compliance_cannot_approve_and_others_cannot_veto(db, make_user) -> None:
    proposer = make_user(["catalog_owner"])
    compliance = make_user(["compliance"])
    warehouse = make_user(["warehouse_staff"])

    def _fresh_item() -> WorkItem:
        result = dispatch_command(
            db,
            scope=f"veto-{uuid.uuid4()}",
            key=f"key-{uuid.uuid4()}",
            command_type="catalog-revision",
            payload={"sku": "SKU-VETO-2"},
            actor_user_id=proposer,
        )
        return _work_items(db, uuid.UUID(result["workflowId"]))[0]

    item = _fresh_item()
    with pytest.raises(PermissionDeniedError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=compliance,
            decision="approve",
            expected_workflow_version=item.expected_version,
        )
    item = _fresh_item()
    with pytest.raises(PermissionDeniedError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=warehouse,
            decision="reject",
            expected_workflow_version=item.expected_version,
        )


def test_decision_audit_rows_appended(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    _, item = _dispatch_procurement(db, proposer)
    submit_decision(
        db,
        work_item_id=item.id,
        user_id=budget_owner,
        decision="approve",
        reason="ok",
        expected_workflow_version=item.expected_version,
    )
    db.flush()
    rows = (
        db.execute(select(WorkItemDecision).where(WorkItemDecision.work_item_id == item.id))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].decision.value == "approve"
    assert rows[0].user_id == budget_owner
    assert rows[0].submitted_version == item.expected_version

    from app.models.audit import AuditLog

    audit = db.execute(select(AuditLog)).scalars().all()
    assert any(entry.action == "work_item.decision" for entry in audit)


def test_deciding_a_non_pending_item_is_conflict(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    budget_owner = make_user(["budget_owner"])
    _, item = _dispatch_procurement(db, proposer)
    submit_decision(
        db,
        work_item_id=item.id,
        user_id=budget_owner,
        decision="approve",
        expected_workflow_version=item.expected_version,
    )
    with pytest.raises(ConflictError, match="not pending"):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=budget_owner,
            decision="approve",
            expected_workflow_version=item.expected_version,
        )


def test_unknown_work_item_kind_and_role_rejected(db, make_user) -> None:
    proposer = make_user(["procurement_lead"])
    run_id, _ = _dispatch_procurement(db, proposer)
    with pytest.raises(ValidationError):
        create_work_item(
            db,
            workflow_id=run_id,
            kind="no-such-kind",
            title="bad",
        )
    with pytest.raises(ValidationError):
        create_work_item(
            db,
            workflow_id=run_id,
            kind="approval",
            title="bad role",
            required_roles=["no_such_role"],
        )
