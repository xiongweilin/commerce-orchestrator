"""DBOS v2 mainline: accept_command, definition registry, inbox relay,
durable approvals and the privacy jobs (P7 WP4)."""

from __future__ import annotations

import os
import subprocess
import sys
import types
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import (
    ConflictError,
    IdempotencyConflictError,
    ValidationError,
    VersionConflictError,
)
from app.core.time import utc_now
from app.models.catalog import CatalogRevision
from app.models.effect import EffectLedgerEntry
from app.models.messaging import (
    IdempotencyRecord,
    InboxEvent,
    InboxStatus,
    OutboxEvent,
)
from app.models.order import SalesOrder
from app.models.returns import ReturnCase
from app.models.sensitive_payload import SensitivePayload
from app.models.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
    WorkItem,
    WorkItemDecision,
    WorkItemStatus,
)
from app.services.approvals import (
    DecisionResult,
    create_work_item,
    submit_decision,
)
from app.services.commands import accept_command
from app.services.outbox_inbox import (
    claim_inbox_batch,
    exponential_backoff_seconds,
    recover_expired_leases,
    relay_inbox_batch,
)


def _count(db, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return db.execute(stmt).scalar_one()


def _accept(
    db,
    *,
    command_type: str = "catalog-revision",
    payload: dict | None = None,
    key: str = "key-1",
    actor_user_id: uuid.UUID | None = None,
    correlation_id: str = "corr-v2",
):
    return accept_command(
        db,
        command={"type": command_type, "payload": payload or {"sku": "SKU-V2"}},
        actor_user_id=actor_user_id,
        idempotency_key=key,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# accept_command
# ---------------------------------------------------------------------------


def test_accept_command_creates_dbos_v2_run_and_worker_event(db, make_user) -> None:
    actor = make_user(["catalog_owner"])
    accepted = _accept(
        db,
        payload={"sku": "SKU-A", "proposed": {"title": "T"}},
        actor_user_id=actor,
    )

    run = db.get(WorkflowRun, accepted.workflow_id)
    assert run is not None
    assert run.workflow_type == "catalog-revision"
    assert run.workflow_version == 2
    assert run.orchestration_engine == "dbos"
    assert run.status == WorkflowRunStatus.ACCEPTED
    assert run.initiated_by_user_id == actor
    assert run.correlation_id == "corr-v2"
    assert run.input_json == {"sku": "SKU-A", "proposed": {"title": "T"}}

    # The workflow.accepted event is routed to the worker inbox (minimal payload).
    outbox = db.execute(select(OutboxEvent)).scalars().one()
    assert outbox.event_type == "workflow.accepted"
    assert outbox.payload == {
        "workflow_id": str(run.id),
        "workflow_type": "catalog-revision",
        "workflow_version": 2,
        "correlation_id": "corr-v2",
    }
    inbox = db.execute(
        select(InboxEvent).where(InboxEvent.consumer == "worker")
    ).scalars().one()
    assert inbox.status == InboxStatus.PENDING
    assert inbox.event_id == outbox.event_id

    # Accept only: no domain entity, no effect, no continuation.
    assert _count(db, CatalogRevision) == 0
    assert _count(db, EffectLedgerEntry) == 0
    assert _count(db, WorkItem) == 0
    # Idempotency record uses the fixed "completed" vocabulary.
    idem = db.execute(select(IdempotencyRecord)).scalar_one()
    assert idem.status == "completed"
    assert accepted.as_dict()["statusUrl"] == f"/v1/workflows/{run.id}"


def test_accept_command_replays_idempotently(db, make_user) -> None:
    actor = make_user(["catalog_owner"])
    first = _accept(db, key="replay-key", actor_user_id=actor)
    second = _accept(db, key="replay-key", actor_user_id=actor)

    assert second.workflow_id == first.workflow_id
    assert second.replayed is True
    assert first.replayed is False
    assert _count(db, WorkflowRun) == 1
    assert _count(db, IdempotencyRecord) == 1


def test_accept_command_same_key_different_body_conflicts(db, make_user) -> None:
    actor = make_user(["catalog_owner"])
    _accept(db, key="conflict-key", payload={"sku": "SKU-1"}, actor_user_id=actor)
    with pytest.raises(IdempotencyConflictError):
        _accept(db, key="conflict-key", payload={"sku": "SKU-2"}, actor_user_id=actor)
    assert _count(db, WorkflowRun) == 1


def test_accept_command_validation(db, make_user) -> None:
    actor = make_user(["catalog_owner"])
    with pytest.raises(ValidationError, match="idempotency_key"):
        accept_command(
            db,
            command={"type": "procurement", "payload": {}},
            actor_user_id=actor,
            idempotency_key="",
            correlation_id="c",
        )
    with pytest.raises(ValidationError, match="unknown command type"):
        _accept(db, command_type="no-such-command", key="k2", actor_user_id=actor)
    assert _count(db, WorkflowRun) == 0


# ---------------------------------------------------------------------------
# (workflow_type, workflow_version) definition registry
# ---------------------------------------------------------------------------


def test_definition_registry_resolves_v2() -> None:
    """Verify the (type, version) registry in a subprocess.

    ``app.workflows.definitions`` imports ``dbos`` at module level; running the
    check in a subprocess keeps this test file from polluting sys.modules for
    ``test_no_dbos_import`` / ``test_worker_import`` in the same pytest run.
    """
    script = """
from app.workflows.definitions import (
    WORKFLOW_DEFINITIONS,
    definition_names,
    resolve_definition,
)
types = (
    "catalog-revision",
    "listing-publication",
    "procurement",
    "return",
    "reconciliation",
    "order-to-cash",
    "return-to-refund",
)
for workflow_type in types:
    fn = resolve_definition(workflow_type, 2)
    assert callable(fn), workflow_type
    assert (workflow_type, 2) in WORKFLOW_DEFINITIONS, workflow_type
try:
    resolve_definition("procurement", 1)
    raise SystemExit("expected ValueError for (procurement, 1)")
except ValueError:
    pass
print(",".join(sorted(definition_names())))
"""
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "procurement@2" in proc.stdout
    assert "return@2" in proc.stdout
    assert "order-to-cash@2" in proc.stdout
    assert "return-to-refund@2" in proc.stdout


# ---------------------------------------------------------------------------
# Inbox relay
# ---------------------------------------------------------------------------


def test_claim_inbox_batch_marks_processing_with_lease(
    clean_outbox_registries, db
) -> None:
    from app.services.outbox_inbox import emit_event

    for i in range(3):
        emit_event(
            db,
            event_type="workflow.accepted",
            aggregate_type="workflow",
            aggregate_id=f"w-{i}",
            producer="workflow",
            payload={"workflow_id": f"w-{i}"},
            consumers=["worker"],
        )
    db.commit()

    claimed = claim_inbox_batch(db, consumer="worker", batch=2, lease_seconds=30)
    assert len(claimed) == 2
    assert all(row.status == InboxStatus.PROCESSING for row in claimed)
    assert all(row.lease_until is not None for row in claimed)
    assert _count(db, InboxEvent, InboxEvent.status == InboxStatus.PROCESSING) == 2
    # The third row stays pending (batch limit).
    assert _count(db, InboxEvent, InboxEvent.status == InboxStatus.PENDING) == 1


def test_claim_skips_rows_not_yet_due(clean_outbox_registries, db) -> None:
    from app.services.outbox_inbox import emit_event

    emit_event(
        db,
        event_type="workflow.accepted",
        aggregate_type="workflow",
        aggregate_id="w-1",
        producer="workflow",
        payload={"workflow_id": "w-1"},
        consumers=["worker"],
    )
    row = db.execute(select(InboxEvent)).scalar_one()
    row.next_attempt_at = utc_now() + timedelta(minutes=5)
    db.commit()

    assert claim_inbox_batch(db, consumer="worker") == []


def test_recover_expired_leases_returns_rows_to_pending(db) -> None:
    from app.models.messaging import InboxStatus
    from app.services.outbox_inbox import emit_event

    emit_event(
        db,
        event_type="workflow.accepted",
        aggregate_type="workflow",
        aggregate_id="w-1",
        producer="workflow",
        payload={"workflow_id": "w-1"},
        consumers=["worker"],
    )
    row = db.execute(select(InboxEvent)).scalar_one()
    row.status = InboxStatus.PROCESSING
    row.lease_until = utc_now() - timedelta(seconds=1)
    db.commit()

    assert recover_expired_leases(db, consumer="worker") == 1
    db.refresh(row)
    assert row.status == InboxStatus.PENDING
    assert row.lease_until is None


def test_relay_batch_processed_retried_and_dead_lettered(
    clean_outbox_registries, db
) -> None:
    from app.services.outbox_inbox import emit_event

    def _emit(event_id: str, event_type: str = "workflow.accepted") -> uuid.UUID:
        emit_event(
            db,
            event_id=uuid.UUID(event_id),
            event_type=event_type,
            aggregate_type="workflow",
            aggregate_id=event_id,
            producer="workflow",
            payload={
                "workflow_id": event_id,
                "workflow_type": "procurement",
                "workflow_version": 2,
            },
            consumers=["worker"],
        )
        db.commit()
        return uuid.UUID(event_id)

    ok_id = _emit("00000000-0000-0000-0000-000000000001")
    retry_id = _emit("00000000-0000-0000-0000-000000000002")
    dead_id = _emit("00000000-0000-0000-0000-000000000003")
    # Simulate one previous failure so the next failure dead-letters at
    # max_attempts=2.
    dead_row = db.execute(
        select(InboxEvent).where(InboxEvent.event_id == dead_id)
    ).scalar_one()
    dead_row.attempts = 1
    db.commit()

    def _dispatch(event) -> None:
        if event.event_id == ok_id:
            return
        if event.event_id == retry_id:
            raise RuntimeError("retry me")
        raise RuntimeError("dead letter me")

    stats = relay_inbox_batch(
        db,
        consumer="worker",
        dispatch=_dispatch,
        max_attempts=2,
    )
    assert stats.claimed == 3
    assert stats.processed == 1
    assert stats.retried == 1
    assert stats.dead_lettered == 1

    ok_row = db.get(InboxEvent, db.execute(
        select(InboxEvent).where(InboxEvent.event_id == ok_id)
    ).scalar_one().id)
    retry_row = db.get(InboxEvent, db.execute(
        select(InboxEvent).where(InboxEvent.event_id == retry_id)
    ).scalar_one().id)
    dead_row = db.get(InboxEvent, db.execute(
        select(InboxEvent).where(InboxEvent.event_id == dead_id)
    ).scalar_one().id)

    assert ok_row.status == InboxStatus.PROCESSED
    assert ok_row.processed_at is not None
    assert retry_row.status == InboxStatus.PENDING
    assert retry_row.attempts == 1
    assert retry_row.next_attempt_at is not None
    assert dead_row.status == InboxStatus.FAILED
    assert dead_row.attempts == 2
    assert dead_row.last_error is not None


def test_exponential_backoff_seconds() -> None:
    assert exponential_backoff_seconds(1) == 1.0
    assert exponential_backoff_seconds(2) == 2.0
    assert exponential_backoff_seconds(3) == 4.0
    assert exponential_backoff_seconds(10) == 60.0  # capped
    assert exponential_backoff_seconds(0) == 0.0


def test_emit_event_carries_trace_fields(clean_outbox_registries, db) -> None:
    from app.services.outbox_inbox import emit_event, envelope_for

    event = emit_event(
        db,
        event_type="workflow.decision_recorded",
        aggregate_type="workflow",
        aggregate_id="w-1",
        producer="workflow",
        payload={"workflow_id": "w-1"},
        consumers=["worker"],
        traceparent="00-11111111111111111111111111111111-2222222222222222-01",
        tracestate="vendor=abc",
    )
    envelope = envelope_for(event)
    assert envelope["traceparent"] == "00-11111111111111111111111111111111-2222222222222222-01"
    assert envelope["tracestate"] == "vendor=abc"
    assert "traceparent" not in envelope["payload"]


# ---------------------------------------------------------------------------
# Durable approval (v2 engine)
# ---------------------------------------------------------------------------


def _dbos_run_with_item(db, make_user) -> tuple[uuid.UUID, uuid.UUID, WorkItem]:
    actor = make_user(["procurement_lead"])
    accepted = _accept(
        db,
        command_type="procurement",
        payload={"sku": "SKU-P", "supplier": "ACME", "qty": "2", "unit_cost": "1.5"},
        key=f"approve-{uuid.uuid4()}",
        actor_user_id=actor,
    )
    run = db.get(WorkflowRun, accepted.workflow_id)
    item = create_work_item(
        db,
        workflow_id=run.id,
        kind="approval",
        title="Approve PO",
        required_roles=["budget_owner"],
        payload={
            "po_id": str(uuid.uuid4()),
            "proposed_by_user_id": str(actor),
            "four_eyes_area": "po",
            "next_step": "approve_po",
        },
        expected_version=run.version,
    )
    db.commit()
    return run.id, actor, item


def test_submit_decision_v2_records_and_emits_decision_recorded(db, make_user) -> None:
    run_id, actor, item = _dbos_run_with_item(db, make_user)
    approver = make_user(["budget_owner"])

    result = submit_decision(
        db,
        work_item_id=item.id,
        user_id=approver,
        decision="approve",
        reason="ok",
        expected_workflow_version=item.expected_version,
        idempotency_key="decision-key-1",
    )
    assert isinstance(result, DecisionResult)
    assert result.decision_recorded is True
    assert result["decisionRecorded"] is True
    assert result["workItemId"] == str(item.id)
    assert result["workflowId"] == str(run_id)
    assert result.replayed is False

    db.refresh(item)
    assert item.status == WorkItemStatus.APPROVED
    assert item.decided_by_user_id == approver
    assert item.version == 2  # item state changed once

    # v2: the run version is bumped by the workflow continuation, not here.
    run = db.get(WorkflowRun, run_id)
    assert run.status == WorkflowRunStatus.ACCEPTED
    assert run.version == 1

    decision = db.execute(
        select(WorkItemDecision).where(WorkItemDecision.work_item_id == item.id)
    ).scalar_one()
    assert decision.decision.value == "approve"
    assert decision.submitted_version == item.expected_version

    # decision_recorded inbox row for the worker (durable message relay).
    inbox = db.execute(
        select(InboxEvent).where(InboxEvent.consumer == "worker")
    ).scalars().all()
    decision_rows = [
        row
        for row in inbox
        if db.get(OutboxEvent, row.event_id).event_type == "workflow.decision_recorded"
    ]
    assert len(decision_rows) == 1
    outbox = db.get(OutboxEvent, decision_rows[0].event_id)
    assert outbox.event_type == "workflow.decision_recorded"
    payload = outbox.payload
    assert payload["workflow_id"] == str(run_id)
    assert payload["work_item_id"] == str(item.id)
    assert payload["decision_id"] == str(decision.id)
    assert payload["decision"] == "approve"
    assert payload["actor_user_id"] == str(approver)

    idem = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == f"work-item-decision:{item.id}"
        )
    ).scalar_one()
    assert idem.status == "completed"
    assert idem.result_json["decisionRecorded"] is True


def test_submit_decision_v2_replay_and_conflict(db, make_user) -> None:
    run_id, actor, item = _dbos_run_with_item(db, make_user)
    approver = make_user(["budget_owner"])
    kwargs = dict(
        work_item_id=item.id,
        user_id=approver,
        decision="approve",
        expected_workflow_version=item.expected_version,
    )
    first = submit_decision(db, idempotency_key="dk-replay", **kwargs)
    replay = submit_decision(db, idempotency_key="dk-replay", **kwargs)
    assert replay.replayed is True
    assert replay["workItemId"] == first["workItemId"]
    assert _count(db, WorkItemDecision) == 1

    with pytest.raises(IdempotencyConflictError):
        submit_decision(db, idempotency_key="dk-replay", **{**kwargs, "reason": "different"})

    # A second concurrent-style submission on the same item loses.
    with pytest.raises(ConflictError):
        submit_decision(db, idempotency_key="dk-new", **kwargs)


def test_submit_decision_v2_version_conflict(db, make_user) -> None:
    run_id, actor, item = _dbos_run_with_item(db, make_user)
    approver = make_user(["budget_owner"])
    with pytest.raises(VersionConflictError):
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=approver,
            decision="approve",
            expected_workflow_version=item.expected_version + 5,
            idempotency_key="dk-version",
        )
    assert _count(db, WorkItemDecision) == 0


# ---------------------------------------------------------------------------
# Inbox dispatch planning (worker relay)
# ---------------------------------------------------------------------------


def test_plan_inbox_action_start_workflow() -> None:
    from app.models.messaging import OutboxEvent
    from app.workflows.inbox_dispatch import plan_inbox_action

    event = OutboxEvent(
        event_type="workflow.accepted",
        aggregate_type="workflow",
        aggregate_id="w-1",
        payload={
            "workflow_id": "w-1",
            "workflow_type": "procurement",
            "workflow_version": 2,
        },
    )
    action = plan_inbox_action(event)
    assert action.kind == "start"
    assert action.workflow_id == "w-1"
    assert action.workflow_type == "procurement"
    assert action.workflow_version == 2
    assert action.start_kwargs == {"workflow_id": "w-1"}


def test_plan_inbox_action_send_decision() -> None:
    from app.models.messaging import OutboxEvent
    from app.workflows.inbox_dispatch import plan_inbox_action

    event = OutboxEvent(
        event_type="workflow.decision_recorded",
        aggregate_type="workflow",
        aggregate_id="w-9",
        payload={
            "workflow_id": "w-9",
            "work_item_id": "it-1",
            "decision_id": "dec-1",
            "decision": "approve",
            "actor_user_id": "u-1",
            "reason": "ok",
            "submitted_version": 2,
        },
    )
    action = plan_inbox_action(event)
    assert action.kind == "send"
    assert action.destination_id == "w-9"
    assert action.topic == "it-1"
    assert action.idempotency_key == "dec-1"
    assert action.message == {
        "work_item_id": "it-1",
        "decision_id": "dec-1",
        "decision": "approve",
        "actor_user_id": "u-1",
        "reason": "ok",
        "submitted_version": 2,
    }


def test_plan_inbox_action_unknown_event_raises() -> None:
    from app.models.messaging import OutboxEvent
    from app.workflows.inbox_dispatch import plan_inbox_action

    event = OutboxEvent(
        event_type="no.such.event",
        aggregate_type="x",
        aggregate_id="1",
        payload={},
    )
    with pytest.raises(ValueError, match="no inbox action"):
        plan_inbox_action(event)


def test_plan_inbox_action_webhook_domain_events_have_no_relay_action() -> None:
    """Webhooks now create DBOS v2 runs; the worker relay is driven by
    ``workflow.accepted``, so planning a raw webhook domain event fails
    closed (webhook domain events never start a workflow directly)."""
    from app.models.messaging import OutboxEvent
    from app.workflows.inbox_dispatch import plan_inbox_action

    for event_type in ("order.received", "return.case_requested"):
        event = OutboxEvent(
            event_type=event_type,
            aggregate_type="sales_order",
            aggregate_id="1",
            payload={"webhook_id": "w-1", "entity_id": "e-1"},
        )
        with pytest.raises(ValueError, match="no inbox action"):
            plan_inbox_action(event)


def test_execute_inbox_action_send_uses_topic_and_idempotency_key(
    monkeypatch,
) -> None:
    from app.workflows.inbox_dispatch import InboxAction, execute_inbox_action

    sent: list[dict] = []
    # Inject a fake dbos module so the lazy import inside execute_inbox_action
    # resolves without pulling the real DBOS runtime into this process.
    fake_dbos = types.ModuleType("dbos")
    fake_dbos.DBOS = type(
        "DBOS",
        (),
        {"send": staticmethod(lambda **kwargs: sent.append(kwargs))},
    )
    monkeypatch.setitem(sys.modules, "dbos", fake_dbos)
    execute_inbox_action(
        InboxAction(
            kind="send",
            destination_id="w-9",
            topic="it-1",
            message={"decision": "approve"},
            idempotency_key="dec-1",
        )
    )
    assert sent == [
        {
            "destination_id": "w-9",
            "topic": "it-1",
            "message": {"decision": "approve"},
            "idempotency_key": "dec-1",
        }
    ]


# ---------------------------------------------------------------------------
# Privacy jobs
# ---------------------------------------------------------------------------


def test_hmac_ref_is_deterministic_and_marked(monkeypatch) -> None:
    from app.services import privacy

    monkeypatch.setattr(privacy, "_pii_hash_key", lambda: b"test-hash-key")
    a = privacy.hmac_ref("customer@example.com")
    b = privacy.hmac_ref("customer@example.com")
    assert a == b
    assert a.startswith("pii:")
    assert privacy.is_pseudonymous(a)
    assert not privacy.is_pseudonymous("customer@example.com")


def test_backfill_customer_refs_encrypts_and_is_idempotent(db, monkeypatch) -> None:
    from app.services import privacy

    monkeypatch.setattr(privacy, "_pii_hash_key", lambda: b"test-hash-key")
    order = SalesOrder(
        order_ref="SO-1",
        shopify_order_id="9001",
        customer_ref="alice@example.com",
        status="received",
        currency="CNY",
        total="99.00",
        shipping={"address1": "Secret St 1", "city": "Shanghai"},
    )
    case = ReturnCase(
        return_ref="RET-1",
        shopify_order_id="9001",
        customer_ref="alice@example.com",
        status="requested",
        currency="CNY",
        refund_amount="10.00",
    )
    db.add_all([order, case])
    db.commit()

    stats = privacy.backfill_customer_refs(db)
    assert stats.sales_orders == 1
    assert stats.return_cases == 1
    assert stats.shipping_rows == 1
    assert stats.errors == []

    db.refresh(order)
    db.refresh(case)
    assert privacy.is_pseudonymous(order.customer_ref)
    assert privacy.is_pseudonymous(case.customer_ref)
    assert order.shipping["encrypted"] is True
    assert "address1" not in order.shipping

    vault = db.execute(select(SensitivePayload)).scalars().all()
    assert len(vault) == 3
    assert all(row.ciphertext for row in vault)
    assert all(row.expires_at is not None for row in vault)
    # Ciphertext is not plaintext.
    assert all("alice@example.com" not in row.ciphertext for row in vault)

    # Second pass is a no-op.
    again = privacy.backfill_customer_refs(db)
    assert again.total == 0


def test_cleanup_expired_payloads_clears_ciphertext_and_tombstones(db) -> None:
    from app.services import privacy

    expired = privacy.store_sensitive_payload(
        db,
        purpose="customer_ref",
        classification="PII",
        owner="commerce",
        source_type="sales_order",
        source_id=str(uuid.uuid4()),
        plaintext="expired@example.com",
        expires_days=0,
    )
    fresh = privacy.store_sensitive_payload(
        db,
        purpose="customer_ref",
        classification="PII",
        owner="commerce",
        source_type="sales_order",
        source_id=str(uuid.uuid4()),
        plaintext="fresh@example.com",
        expires_days=30,
    )
    db.commit()

    stats = privacy.cleanup_expired_payloads(db, now=utc_now() + timedelta(hours=1))
    assert stats.cleared == 1
    assert stats.errors == 0
    assert stats.oldest_overdue_age_seconds is not None

    db.refresh(expired)
    db.refresh(fresh)
    assert expired.ciphertext == ""
    assert expired.deleted_at is not None
    assert fresh.ciphertext != ""
    assert fresh.deleted_at is None


def test_should_run_cleanup_due() -> None:
    from app.services.privacy import should_run_cleanup

    now = utc_now()
    assert should_run_cleanup(last_run=None, now=now) is True
    assert (
        should_run_cleanup(
            last_run=now - timedelta(hours=25),
            now=now,
        )
        is True
    )
    assert (
        should_run_cleanup(
            last_run=now - timedelta(hours=1),
            now=now,
        )
        is False
    )
