"""DBOS v2 integration tests (P7 WP7, 整改计划 §六.1).

Requires a reachable local PostgreSQL (Docker ``commerce-postgres``) and the
DBOS runtime; the module skips when either is unavailable.  All state lives in
isolated temporary databases created per module — business data is never
touched.

Covered scenarios:

- command accept -> inbox relay -> DBOS workflow auto-start (v2 mainline);
- replayed ``workflow.accepted`` starts exactly one workflow (deterministic
  ``SetWorkflowID``);
- a recorded decision is delivered to the blocking ``DBOS.recv`` and the
  continuation advances the run;
- effect execution through the typed seam: ``outcome_unknown`` settles the
  run into ``needs_reconciliation`` with no blind retry;
- retryable failures are bounded to 3 attempts (success on the third and
  definitive failure after three attempts);
- worker kill before the decision is relayed: the durable decision survives,
  ``resume_workflows`` recovers the pending run and the continuation applies.

Naming note: this file sorts after ``test_worker_import.py`` /
``test_no_dbos_import.py`` on purpose — importing the DBOS runtime in-process
would pollute ``sys.modules`` for those isolation tests, so this module keeps
every ``dbos`` import lazy (inside fixtures) and runs last in the suite.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.base import EffectResult, OutcomeUnknownError
from app.core.errors import RetryableEffectError
from app.core.uuid7 import uuid7
from app.models.base import Base
from app.models.effect import EffectLedgerEntry, EffectStatus
from app.models.identity import Role, RoleAssignment, User
from app.models.listing import ExternalIdMapping
from app.models.messaging import InboxEvent, OutboxEvent
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem
from app.services.approvals import submit_decision
from app.services.commands import accept_command
from app.services.outbox_inbox import claim_inbox_batch
from app.workflows.inbox_dispatch import execute_inbox_action, plan_inbox_action

pytestmark = pytest.mark.dbos_integration

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PG_ADMIN_URL = "postgresql://commerce:commerce@localhost:5432/postgres"


def _pg_reachable() -> bool:
    try:
        with psycopg.connect(PG_ADMIN_URL, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - skip decision, not a test failure
        return False


PG_REACHABLE = _pg_reachable()


def _dbos_importable() -> bool:
    """Return True when the ``dbos`` package is installed, without importing it.

    A real ``import dbos`` here would pollute ``sys.modules`` for
    ``test_no_dbos_import`` / ``test_worker_import`` (both assert the service
    layer never pulls the runtime in).
    """
    import importlib.util

    return importlib.util.find_spec("dbos") is not None


DBOS_IMPORTABLE = _dbos_importable()


def _create_database(name: str) -> None:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}" OWNER commerce')


def _drop_database(name: str) -> None:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def dbos_env(request: pytest.FixtureRequest) -> Iterator[dict]:
    """Launch an in-process DBOS runtime against fresh temp databases.

    Function-scoped: every test gets its own databases and runtime so row
    counts and workflow states are never polluted by a previous test.  The
    DBOS singleton supports destroy + relaunch (verified: pending workflows
    persist in the system database and ``resume_workflows`` recovers them).
    """
    if not PG_REACHABLE:
        pytest.skip("PostgreSQL unreachable (Docker commerce-postgres required)")
    if not DBOS_IMPORTABLE:
        pytest.skip("dbos runtime not importable")

    from dbos import DBOS, DBOSConfig

    token = secrets.token_hex(4)
    app_db = f"commerce_p7_dbos_{token}"
    sys_db = f"dbos_p7_it_{token}"
    _create_database(app_db)
    _create_database(sys_db)
    app_url = f"postgresql+psycopg://commerce:commerce@localhost:5432/{app_db}"
    sys_url = f"postgresql+psycopg://commerce:commerce@localhost:5432/{sys_db}"

    app_engine = create_engine(app_url, pool_pre_ping=True)
    Base.metadata.create_all(app_engine)
    factory = sessionmaker(
        bind=app_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    DBOS(
        config=DBOSConfig(
            name="commerce-orchestrator",
            system_database_url=sys_url,
            application_database_url=app_url,
            log_level="WARNING",
            dbos_system_schema="dbos",
        )
    )
    # Importing the module registers the v2 definitions (lazy: only here).
    from app.workflows import definitions  # noqa: F401

    DBOS.launch()
    try:
        yield {
            "factory": factory,
            "app_url": app_url,
            "sys_url": sys_url,
        }
    finally:
        DBOS.destroy()
        app_engine.dispose()
        _drop_database(app_db)
        _drop_database(sys_db)
        # DBOS executor threads stay blocked in recv after destroy; tell the
        # sessionfinish hook to exit cleanly after the summary is printed.
        request.config._dbos_force_exit = True


def _make_user(factory: Callable[[], Session], roles: list[str]) -> uuid.UUID:
    """Create a user with roles (separate commits; see WP7-REPORT.md)."""
    user_id = uuid7()
    with factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"dbos-{user_id}@test.local",
                display_name="DBOS User",
            )
        )
        db.commit()
    with factory() as db:
        for role in roles:
            db.add(RoleAssignment(user_id=user_id, role=Role(role), scope="*"))
        db.commit()
    return user_id


def _accept(
    factory: Callable[[], Session],
    *,
    command_type: str,
    payload: dict,
    actor: uuid.UUID,
    key: str,
) -> uuid.UUID:
    with factory() as db:
        accepted = accept_command(
            db,
            command={"type": command_type, "payload": payload},
            actor_user_id=actor,
            idempotency_key=key,
            correlation_id=f"corr-{key}",
        )
        db.commit()
        return accepted.workflow_id


def _relay_all(factory: Callable[[], Session], *, consumer: str = "worker") -> int:
    """Claim the worker inbox and execute every planned DBOS action."""
    dispatched = 0
    with factory() as db:
        claimed = claim_inbox_batch(db, consumer=consumer, batch=50, lease_seconds=30)
        for row in claimed:
            event = db.get(OutboxEvent, row.event_id)
            action = plan_inbox_action(event)
            execute_inbox_action(action)
            dispatched += 1
        db.commit()
    return dispatched


def _wait_for(fn: Callable[[], bool], *, timeout: float = 30.0, step: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(step)
    return False


def _items_for(factory: Callable[[], Session], run_id: uuid.UUID) -> list[WorkItem]:
    with factory() as db:
        return list(
            db.execute(
                select(WorkItem)
                .where(WorkItem.workflow_id == run_id)
                .order_by(WorkItem.created_at)
            )
            .scalars()
            .all()
        )


def _run_status(factory: Callable[[], Session], run_id: uuid.UUID) -> str | None:
    with factory() as db:
        run = db.get(WorkflowRun, run_id)
        return run.status.value if run else None


def _start_catalog_run(
    factory: Callable[[], Session],
    actor: uuid.UUID,
    *,
    sku: str,
    key: str,
) -> tuple[uuid.UUID, WorkItem]:
    run_id = _accept(
        factory,
        command_type="catalog-revision",
        payload={"sku": sku, "proposed": {"title": "T"}},
        actor=actor,
        key=key,
    )
    assert _relay_all(factory) == 1
    assert _wait_for(lambda: len(_items_for(factory, run_id)) == 1), "work item not created"
    item = _items_for(factory, run_id)[0]
    return run_id, item


# ---------------------------------------------------------------------------
# v2 mainline
# ---------------------------------------------------------------------------


def test_v2_accept_start_reaches_awaiting_approval(dbos_env) -> None:
    factory = dbos_env["factory"]
    actor = _make_user(factory, ["catalog_owner"])
    run_id = _accept(
        factory,
        command_type="catalog-revision",
        payload={"sku": "SKU-DBOS-1"},
        actor=actor,
        key="dbos-key-1",
    )
    with factory() as db:
        run = db.get(WorkflowRun, run_id)
        assert run.status == WorkflowRunStatus.ACCEPTED
        assert run.orchestration_engine == "dbos"
        assert run.workflow_version == 2

    assert _relay_all(factory) == 1  # workflow.accepted -> start
    assert _wait_for(
        lambda: _run_status(factory, run_id) == "awaiting_approval"
    ), "workflow did not reach awaiting_approval"
    items = _items_for(factory, run_id)
    assert len(items) == 1
    assert items[0].required_roles == ["catalog_owner"]
    with factory() as db:
        run = db.get(WorkflowRun, run_id)
        assert run.version >= 2  # accepted -> running bumps the CAS version
        assert items[0].expected_version == run.version

    # The DBOS system records one execution for the deterministic id.
    from dbos import DBOS

    status = DBOS.get_workflow_status(str(run_id))
    assert status is not None


def test_accepted_replay_starts_single_workflow(dbos_env) -> None:
    factory = dbos_env["factory"]
    actor = _make_user(factory, ["catalog_owner"])
    run_id, item = _start_catalog_run(factory, actor, sku="SKU-DBOS-2", key="dbos-key-2")

    # Replay the same workflow.accepted event 9 more times (at-least-once
    # redelivery / lease-expiry re-dispatch): SetWorkflowID determinism must
    # return the original execution instead of starting a second workflow.
    with factory() as db:
        event = (
            db.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "workflow.accepted",
                    OutboxEvent.aggregate_id == str(run_id),
                )
            )
            .scalars()
            .one()
        )
        for _ in range(9):
            execute_inbox_action(plan_inbox_action(event))
        db.commit()

    # Still one run, one work item, still waiting at the approval gate.
    assert _wait_for(lambda: _run_status(factory, run_id) == "awaiting_approval")
    with factory() as db:
        runs = db.execute(select(func.count()).select_from(WorkflowRun)).scalar_one()
        items = db.execute(
            select(func.count())
            .select_from(WorkItem)
            .where(WorkItem.workflow_id == run_id)
        ).scalar_one()
        assert runs == 1
        assert items == 1
    assert _items_for(factory, run_id)[0].id == item.id


def test_decision_relay_advances_workflow(dbos_env) -> None:
    factory = dbos_env["factory"]
    proposer = _make_user(factory, ["procurement_lead"])
    approver = _make_user(factory, ["budget_owner"])
    run_id = _accept(
        factory,
        command_type="procurement",
        payload={"sku": "SKU-DBOS-3", "qty": "2", "supplier": "ACME", "unit_cost": "1.5"},
        actor=proposer,
        key="dbos-key-3",
    )
    assert _relay_all(factory) == 1
    assert _wait_for(lambda: len(_items_for(factory, run_id)) == 1)
    item = _items_for(factory, run_id)[0]
    assert item.required_roles == ["budget_owner"]

    with factory() as db:
        outcome = submit_decision(
            db,
            work_item_id=item.id,
            user_id=approver,
            decision="approve",
            reason="ok",
            expected_workflow_version=item.expected_version,
            idempotency_key="dbos-dec-3",
        )
        db.commit()
        assert outcome.decision_recorded is True

    assert _relay_all(factory) == 1  # workflow.decision_recorded -> DBOS.send
    assert _wait_for(lambda: len(_items_for(factory, run_id)) == 2), (
        "continuation did not create the next work item"
    )
    items = _items_for(factory, run_id)
    assert items[1].kind.value == "confirmation"
    assert items[1].required_roles == ["warehouse_staff"]
    assert _run_status(factory, run_id) == "awaiting_approval"


# ---------------------------------------------------------------------------
# Typed effect execution through the DBOS driver (计划 §二.4)
# ---------------------------------------------------------------------------


class _ScriptedShopify:
    """Test adapter scripting per-call outcomes for ``product_publish``."""

    name = "shopify"

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls = 0

    def publish_product(self, **kwargs):
        self.calls += 1
        outcome = self._script[min(self.calls - 1, len(self._script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _seed_gid_mapping(factory: Callable[[], Session], sku: str) -> None:
    with factory() as db:
        db.add(
            ExternalIdMapping(
                sku=sku,
                channel="shopify",
                external_id=f"gid://shopify/Product/{sku}",
            )
        )
        db.commit()


def _drive_catalog_to_effect(
    factory: Callable[[], Session],
    *,
    actor: uuid.UUID,
    sku: str,
    key: str,
    stub: _ScriptedShopify,
) -> uuid.UUID:
    """Run a catalog-revision approval through the effect stage with ``stub``."""
    from app.connectors import registry

    _seed_gid_mapping(factory, sku)
    registry._SINGLETONS["shopify"] = stub
    run_id, item = _start_catalog_run(factory, actor, sku=sku, key=key)
    with factory() as db:
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=actor,
            decision="approve",
            reason="ok",
            expected_workflow_version=item.expected_version,
            idempotency_key=f"{key}-dec",
        )
        db.commit()
    assert _relay_all(factory) == 1
    return run_id


def test_outcome_unknown_settles_needs_reconciliation_no_retry(dbos_env) -> None:
    factory = dbos_env["factory"]
    actor = _make_user(factory, ["catalog_owner"])
    stub = _ScriptedShopify([OutcomeUnknownError("remote state ambiguous")])
    run_id = _drive_catalog_to_effect(
        factory, actor=actor, sku="SKU-DBOS-OU", key="dbos-ou", stub=stub
    )
    assert _wait_for(
        lambda: _run_status(factory, run_id) == "needs_reconciliation",
        timeout=40,
    ), "run did not settle into needs_reconciliation"
    assert stub.calls == 1  # outcome_unknown is never blind-retried
    with factory() as db:
        entry = db.execute(select(EffectLedgerEntry)).scalar_one()
        assert entry.status == EffectStatus.OUTCOME_UNKNOWN
        assert entry.attempt == 1
        assert entry.compensation == "reconciliation"


def test_retryable_effect_bounded_to_three_attempts(dbos_env) -> None:
    factory = dbos_env["factory"]
    actor = _make_user(factory, ["catalog_owner"])

    # Two transient failures then success: the third attempt applies.
    stub_ok = _ScriptedShopify(
        [
            RetryableEffectError("rate limited 1"),
            RetryableEffectError("rate limited 2"),
            EffectResult.succeeded("gid://shopify/Product/SKU-DBOS-R", "h"),
        ]
    )
    run_id = _drive_catalog_to_effect(
        factory, actor=actor, sku="SKU-DBOS-R", key="dbos-retry-ok", stub=stub_ok
    )
    assert _wait_for(
        lambda: _run_status(factory, run_id) == "completed",
        timeout=60,
    ), "retryable run did not complete"
    assert stub_ok.calls == 3
    with factory() as db:
        entry = db.execute(select(EffectLedgerEntry)).scalar_one()
        assert entry.status == EffectStatus.SUCCEEDED
        assert entry.attempt == 3

    # Persistent transient failures exhaust the 3-attempt budget -> failed.
    stub_fail = _ScriptedShopify([RetryableEffectError("rate limited")])
    run_id2 = _drive_catalog_to_effect(
        factory, actor=actor, sku="SKU-DBOS-F", key="dbos-retry-fail", stub=stub_fail
    )
    assert _wait_for(
        lambda: _run_status(factory, run_id2) == "failed",
        timeout=60,
    ), "exhausted-retry run did not fail"
    assert stub_fail.calls == 3
    with factory() as db:
        entry = db.execute(
            select(EffectLedgerEntry)
            .join(WorkflowRun, WorkflowRun.id == EffectLedgerEntry.approval_ref)
            .where(WorkflowRun.id == run_id2)
        ).scalar_one()
        assert entry.status == EffectStatus.FAILED
        assert entry.attempt == 3


# ---------------------------------------------------------------------------
# Worker kill / recovery (计划 §二.2: durable decision messaging)
# ---------------------------------------------------------------------------


def test_worker_kill_recovery_resumes_pending_workflow(dbos_env) -> None:
    factory = dbos_env["factory"]
    proposer = _make_user(factory, ["procurement_lead"])
    approver = _make_user(factory, ["budget_owner"])
    run_id = _accept(
        factory,
        command_type="procurement",
        payload={"sku": "SKU-DBOS-K", "qty": "1", "supplier": "ACME", "unit_cost": "1"},
        actor=proposer,
        key="dbos-key-kill",
    )
    assert _relay_all(factory) == 1
    assert _wait_for(lambda: len(_items_for(factory, run_id)) == 1)
    item = _items_for(factory, run_id)[0]

    # Record the decision (durable) but kill the worker before relaying it.
    with factory() as db:
        submit_decision(
            db,
            work_item_id=item.id,
            user_id=approver,
            decision="approve",
            reason="ok",
            expected_workflow_version=item.expected_version,
            idempotency_key="dbos-dec-kill",
        )
        db.commit()

    from dbos import DBOS

    DBOS.destroy()  # worker killed: runtime gone, system DB keeps the run

    from dbos import DBOSConfig

    DBOS(
        config=DBOSConfig(
            name="commerce-orchestrator",
            system_database_url=dbos_env["sys_url"],
            application_database_url=dbos_env["app_url"],
            log_level="WARNING",
            dbos_system_schema="dbos",
        )
    )
    DBOS.launch()

    # Relay the durable decision, then recover the pending workflow.
    assert _relay_all(factory) == 1
    handles = DBOS.resume_workflows([str(run_id)])
    assert len(handles) == 1

    assert _wait_for(lambda: len(_items_for(factory, run_id)) == 2, timeout=60), (
        "recovered workflow did not apply the decision continuation"
    )
    items = _items_for(factory, run_id)
    assert items[1].required_roles == ["warehouse_staff"]
    with factory() as db:
        decision_rows = db.execute(select(func.count()).select_from(InboxEvent)).scalar_one()
        run = db.get(WorkflowRun, run_id)
        assert run.status == WorkflowRunStatus.AWAITING_APPROVAL
        assert run.version >= 3
        assert decision_rows >= 2  # accepted + decision_recorded events


__all__ = ["DBOS_IMPORTABLE", "PG_REACHABLE", "dbos_env"]
