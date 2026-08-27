from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

import app.workflows  # noqa: F401 - activates explicit workflow continuation aliases
from app.api.v1.decisions import AuthorizedDecisionSubmit, submit_authorized_work_item_decision
from app.core.errors import IdempotencyConflictError, ValidationError
from app.core.time import utc_now
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.responsibility import ExecutionAuthorization
from app.models.returns import ReturnCase, ReturnDisposition, ReturnStatus
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemDecisionType,
    WorkItemKind,
    WorkItemStatus,
)
from app.services import commands
from app.services.approvals import _STEP_REGISTRY
from app.services.effect_ledger import effect_transition_context, mark_dispatched, record_effect
from app.services.responsibility_execution import (
    RETURN_CREDIT_NOTE_PROFILE,
    RETURN_REFUND_PROFILE,
    issue_work_item_execution_authorization,
)


def test_return_to_refund_uses_existing_return_financial_continuations() -> None:
    """The webhook workflow must not silently drop ReturnCase continuations."""

    registry = _STEP_REGISTRY["return-to-refund"]
    assert registry["approve_credit_note"] is commands._approve_return_credit_note
    assert registry["approve_refund"] is commands._approve_return_refund


def _return_run_and_case(db, actor: uuid.UUID) -> tuple[WorkflowRun, ReturnCase]:
    run = WorkflowRun(
        workflow_type="return-to-refund",
        workflow_version=2,
        orchestration_engine="dbos",
        initiated_by_user_id=actor,
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        input_json={},
    )
    db.add(run)
    db.flush()
    case = ReturnCase(
        return_ref=f"RET-C18-{uuid.uuid4().hex[:8]}",
        order_ref="ORDER-C18",
        shopify_order_id="90018",
        customer_ref="customer-c18",
        reason="damaged",
        status=ReturnStatus.DISPOSITION_APPROVED,
        refund_amount=Decimal("42.50"),
        currency="CNY",
        disposition=ReturnDisposition.RESTOCK,
    )
    db.add(case)
    db.flush()
    return run, case


def _decision_item(
    db,
    *,
    run: WorkflowRun,
    case: ReturnCase,
    actor: uuid.UUID,
    step: str,
) -> tuple[WorkItem, WorkItemDecision]:
    role = "accountant" if step == "approve_credit_note" else "finance_approver"
    item = WorkItem(
        workflow_id=run.id,
        kind=WorkItemKind.APPROVAL,
        title=f"C18 {step}",
        required_roles=[role],
        payload_json={"case_id": str(case.id), "next_step": step},
        status=WorkItemStatus.APPROVED,
        expected_version=run.version,
    )
    db.add(item)
    db.flush()
    decision = WorkItemDecision(
        work_item_id=item.id,
        decision=WorkItemDecisionType.APPROVE,
        user_id=actor,
        submitted_version=run.version,
    )
    db.add(decision)
    db.flush()
    return item, decision


def _authorize(
    db,
    *,
    item: WorkItem,
    decision: WorkItemDecision,
    actor: uuid.UUID,
    expires_at=None,
) -> ExecutionAuthorization:
    authorization, _profile = issue_work_item_execution_authorization(
        db,
        decision=decision,
        item=item,
        actor_user_id=actor,
        listing_scope={"client_must_not_control": "return-financial-scope"},
        expires_at=expires_at,
    )
    return authorization


def test_financial_decision_without_authorization_cannot_plan_effect(db, make_user) -> None:
    actor = make_user(["accountant"])
    run, case = _return_run_and_case(db, actor)
    _decision_item(db, run=run, case=case, actor=actor, step="approve_credit_note")

    with pytest.raises(ValidationError, match="Decision alone is insufficient"):
        record_effect(
            db,
            target_system="odoo",
            operation="credit_note_create",
            approval_ref=run.id,
            idempotency_key="c18:no-credit-auth",
        )


def test_credit_note_and_refund_use_distinct_exact_authorities(db, make_user) -> None:
    accountant = make_user(["accountant"])
    finance = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, accountant)
    credit_item, credit_decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=accountant,
        step="approve_credit_note",
    )
    refund_item, refund_decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=finance,
        step="approve_refund",
    )

    credit_auth = _authorize(
        db,
        item=credit_item,
        decision=credit_decision,
        actor=accountant,
    )
    assert credit_auth.target_system == "odoo"
    assert set(credit_auth.allowed_operations) == set(RETURN_CREDIT_NOTE_PROFILE.allowed_operations)
    assert credit_auth.scope["refund_amount"] == "42.50"
    assert credit_auth.scope["currency"] == "CNY"
    assert "client_must_not_control" not in credit_auth.scope

    # A credit-note authority cannot be rebound to the refund work item.
    refund_item.payload_json = {
        **(refund_item.payload_json or {}),
        "authorization_ref": str(credit_auth.id),
    }
    db.flush()
    with pytest.raises(ValidationError, match="wrong-scope"):
        record_effect(
            db,
            target_system="shopify",
            operation="refund_create",
            approval_ref=run.id,
            idempotency_key="c18:wrong-refund-auth",
        )

    refund_item.payload_json = {
        key: value
        for key, value in (refund_item.payload_json or {}).items()
        if key != "authorization_ref"
    }
    db.flush()
    refund_auth = _authorize(
        db,
        item=refund_item,
        decision=refund_decision,
        actor=finance,
    )
    assert refund_auth.target_system == "shopify"
    assert set(refund_auth.allowed_operations) == set(RETURN_REFUND_PROFILE.allowed_operations)
    assert credit_auth.id != refund_auth.id
    assert credit_auth.decision_ref != refund_auth.decision_ref

    create_effect = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_create",
        approval_ref=run.id,
        idempotency_key="c18:credit-create",
    )
    validate_effect = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_validate",
        approval_ref=run.id,
        idempotency_key="c18:credit-validate",
    )
    refund_effect = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        approval_ref=run.id,
        idempotency_key="c18:refund",
    )
    assert create_effect.authorization_ref == credit_auth.id
    assert validate_effect.authorization_ref == credit_auth.id
    assert refund_effect.authorization_ref == refund_auth.id


def test_credit_note_chained_outputs_do_not_self_invalidate_authority(db, make_user) -> None:
    actor = make_user(["accountant"])
    run, case = _return_run_and_case(db, actor)
    item, decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=actor,
        step="approve_credit_note",
    )
    authorization = _authorize(db, item=item, decision=decision, actor=actor)
    create_effect = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_create",
        approval_ref=run.id,
        idempotency_key="c18:chain-create",
    )
    validate_effect = record_effect(
        db,
        target_system="odoo",
        operation="credit_note_validate",
        approval_ref=run.id,
        idempotency_key="c18:chain-validate",
    )
    mark_dispatched(
        db,
        create_effect.intent_id,
        context=effect_transition_context("odoo.credit_note_create"),
    )

    # These are execution outputs/lifecycle facts and are intentionally absent
    # from the authority fingerprint.
    case.odoo_credit_note_id = "42001"
    case.credit_note_id = "CN-42001"
    case.status = ReturnStatus.CREDIT_NOTE_POSTED
    db.flush()
    mark_dispatched(
        db,
        validate_effect.intent_id,
        context=effect_transition_context("odoo.credit_note_validate"),
    )
    assert validate_effect.authorization_ref == authorization.id
    assert validate_effect.status is EffectStatus.DISPATCHED


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("refund_amount", Decimal("43.00")),
        ("currency", "USD"),
        ("order_ref", "ORDER-CHANGED"),
        ("return_ref", "RET-CHANGED"),
    ],
)
def test_financial_binding_mutation_invalidates_planned_authority(
    db,
    make_user,
    mutation,
    value,
) -> None:
    actor = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, actor)
    item, decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=actor,
        step="approve_refund",
    )
    _authorize(db, item=item, decision=decision, actor=actor)
    effect = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        approval_ref=run.id,
        idempotency_key=f"c18:mutate:{mutation}",
    )
    setattr(case, mutation, value)
    db.flush()
    with pytest.raises(ValidationError, match="stale|rebound|wrong-scope"):
        mark_dispatched(db, effect.intent_id)


def test_return_case_version_change_invalidates_planned_authority(db, make_user) -> None:
    actor = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, actor)
    item, decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=actor,
        step="approve_refund",
    )
    _authorize(db, item=item, decision=decision, actor=actor)
    effect = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        approval_ref=run.id,
        idempotency_key="c18:version-change",
    )
    case.version += 1
    db.flush()
    with pytest.raises(ValidationError, match="stale|rebound"):
        mark_dispatched(db, effect.intent_id)


@pytest.mark.parametrize("mode", ["revoked", "expired"])
def test_authority_change_after_planning_fails_at_physical_dispatch(db, make_user, mode) -> None:
    actor = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, actor)
    item, decision = _decision_item(
        db,
        run=run,
        case=case,
        actor=actor,
        step="approve_refund",
    )
    auth = _authorize(
        db,
        item=item,
        decision=decision,
        actor=actor,
        expires_at=utc_now() + timedelta(hours=1),
    )
    effect = record_effect(
        db,
        target_system="shopify",
        operation="refund_create",
        approval_ref=run.id,
        idempotency_key=f"c18:{mode}",
    )
    if mode == "revoked":
        auth.revoked_at = utc_now()
    else:
        auth.expires_at = utc_now() - timedelta(seconds=1)
    db.flush()
    with pytest.raises(ValidationError, match="expired|revoked"):
        mark_dispatched(db, effect.intent_id)


def test_pre_c18_unbound_effect_is_not_retroactively_backfilled(db, make_user) -> None:
    actor = make_user(["accountant"])
    run, case = _return_run_and_case(db, actor)
    _decision_item(db, run=run, case=case, actor=actor, step="approve_credit_note")
    legacy = EffectLedgerEntry(
        intent_id=uuid.uuid4(),
        target_system="odoo",
        operation="credit_note_create",
        idempotency_key="c18:legacy",
        attempt=0,
        approval_ref=run.id,
        workflow_ref=run.id,
        authorization_ref=None,
        status=EffectStatus.PLANNED,
    )
    db.add(legacy)
    db.flush()
    mark_dispatched(
        db,
        legacy.intent_id,
        context=effect_transition_context("odoo.credit_note_create"),
    )
    assert legacy.authorization_ref is None
    assert legacy.status is EffectStatus.DISPATCHED


def test_authorized_decision_idempotency_replays_same_return_authority(db, make_user) -> None:
    actor = make_user(["finance_approver"])
    run, case = _return_run_and_case(db, actor)
    item = WorkItem(
        workflow_id=run.id,
        kind=WorkItemKind.APPROVAL,
        title="Approve refund",
        required_roles=["finance_approver"],
        payload_json={"case_id": str(case.id), "next_step": "approve_refund"},
        status=WorkItemStatus.PENDING,
        expected_version=run.version,
    )
    db.add(item)
    db.flush()
    body = AuthorizedDecisionSubmit(
        expectedWorkflowVersion=run.version,
        scope={"ignored_for_return": "server-owned"},
    )
    first = submit_authorized_work_item_decision(
        item.id,
        body,
        db,
        actor,
        "c18-authorized-idempotency",
    )
    second = submit_authorized_work_item_decision(
        item.id,
        body,
        db,
        actor,
        "c18-authorized-idempotency",
    )
    assert first.authorizationId == second.authorizationId
    assert first.authorizationProfile == second.authorizationProfile == "return-refund-v1"

    changed = AuthorizedDecisionSubmit(
        expectedWorkflowVersion=run.version,
        scope={"ignored_for_return": "changed-body-still-conflicts"},
    )
    with pytest.raises(IdempotencyConflictError):
        submit_authorized_work_item_decision(
            item.id,
            changed,
            db,
            actor,
            "c18-authorized-idempotency",
        )
