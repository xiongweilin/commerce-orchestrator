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
from app.models.order import SalesOrder, SalesOrderStatus
from app.models.procurement import ProcurementOrder, ProcurementStatus
from app.models.workflow import WorkflowRun, WorkflowRunStatus, WorkItem
from app.services.approvals import submit_decision
from app.services.commands import accept_command
from app.services.outbox_inbox import claim_inbox_batch
from app.services.webhooks import ingest_shopify_webhook
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

    # Default isolation (P7 测试隔离整改): the DBOS worker must never touch
    # the real Shopify/Odoo adapters during the full test run.  Install
    # scripted fakes into the connector registry; individual tests override
    # them with their own scripted adapter when they shape specific
    # outcomes (e.g. ``_drive_catalog_to_effect`` with ``_ScriptedShopify``).
    from app.connectors import registry

    previous_singletons = {
        name: registry._SINGLETONS.get(name) for name in ("shopify", "odoo")
    }
    shopify_fake = _ScriptedShopify(
        [EffectResult.succeeded("gid://shopify/Product/default", "hash:default")]
    )
    odoo_fake = _ScriptedOdoo()
    registry._SINGLETONS["shopify"] = shopify_fake
    registry._SINGLETONS["odoo"] = odoo_fake

    try:
        yield {
            "factory": factory,
            "app_url": app_url,
            "sys_url": sys_url,
            "shopify_fake": shopify_fake,
            "odoo_fake": odoo_fake,
        }
    finally:
        DBOS.destroy()
        # Restore whatever the registry held before the fixture installed the
        # fakes so no other test module observes test adapters.
        for name, prior in previous_singletons.items():
            if prior is None:
                registry._SINGLETONS.pop(name, None)
            else:
                registry._SINGLETONS[name] = prior
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


def _ledger_succeeded_ops(
    factory: Callable[[], Session], run_id: uuid.UUID
) -> set[str]:
    """Operations whose ledger rows for ``run_id`` are terminal-succeeded."""
    with factory() as db:
        rows = (
            db.execute(
                select(EffectLedgerEntry).where(
                    EffectLedgerEntry.approval_ref == run_id
                )
            )
            .scalars()
            .all()
        )
        return {
            row.operation for row in rows if row.status == EffectStatus.SUCCEEDED
        }


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


def _ingest_webhook(
    factory: Callable[[], Session],
    *,
    topic: str,
    payload: dict,
) -> str:
    """Ingest a Shopify webhook and return the created v2 run id."""
    import json

    raw = json.dumps(payload).encode("utf-8")
    with factory() as db:
        result = ingest_shopify_webhook(
            db,
            webhook_id=str(uuid.uuid4()),
            topic=topic,
            raw_body=raw,
            payload=payload,
        )
        db.commit()
    assert result["workflow_version"] == 2
    assert result["workflow_id"] is not None
    return str(result["workflow_id"])


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


def test_webhook_order_starts_v2_definition_and_intakes_to_odoo(dbos_env) -> None:
    """P7 整改第 1 点: a Shopify order webhook creates a DBOS v2 run (never a
    v1 run), the relay starts the ``order-to-cash@2`` definition with the
    deterministic workflow id, and the intake effects (sale_order_create /
    sale_order_confirm) run through the fake Odoo adapter -- the order only
    reaches ``confirmed`` after the remote effects succeed."""
    factory = dbos_env["factory"]
    run_id = _ingest_webhook(
        factory,
        topic="orders/create",
        payload={
            "id": 9001,
            "name": "#S9001",
            "email": "buyer@example.com",
            "currency": "JPY",
            "total_price": "9900.00",
            "line_items": [{"id": 1, "sku": "SKU-W", "quantity": 1}],
        },
    )
    with factory() as db:
        run = db.get(WorkflowRun, uuid.UUID(run_id))
        assert run is not None
        assert run.workflow_type == "order-to-cash"
        assert run.workflow_version == 2
        assert run.orchestration_engine == "dbos"
        # Minimal input: no raw payload expansion (no email / line items).
        blob = str(run.input_json)
        assert "buyer@example.com" not in blob
        assert "line_items" not in blob

    # workflow.accepted is relayed: the v2 definition starts exactly once.
    assert _relay_all(factory) == 1
    assert _wait_for(
        lambda: len(_items_for(factory, uuid.UUID(run_id))) == 1,
        timeout=40,
    ), "order-to-cash v2 run did not create the reservation gate"

    order = None
    with factory() as db:
        order = db.execute(select(SalesOrder)).scalars().one()
        # Intake effects succeed against the fake Odoo; the order reaches
        # confirmed only after sale_order_confirm succeeded (not before).
        assert order.status == SalesOrderStatus.CONFIRMED
        assert order.odoo_sale_order_id is not None

    # The first human gate is the inventory-reservation approval.
    items = _items_for(factory, uuid.UUID(run_id))
    assert items[0].required_roles == ["inventory_supervisor"]
    assert items[0].payload_json["next_step"] == "reserve"
    assert _run_status(factory, uuid.UUID(run_id)) == "awaiting_approval"

    # Ledger: sale_order_create + sale_order_confirm both succeeded with
    # remote references consistent with the domain column.
    ops = _ledger_succeeded_ops(factory, uuid.UUID(run_id))
    assert ops == {"sale_order_create", "sale_order_confirm"}
    assert order.odoo_sale_order_id  # written back by finalize_after_effect


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

    # The planned Odoo effects recorded by the PO approval were executed
    # through the injected fake adapter: po_create + po_confirm both
    # succeeded, and the create effect's remote reference was written back to
    # the purchase order (P7 整改第 3 点 remote id chain).
    assert _wait_for(
        lambda: {"po_create", "po_confirm"} <= _ledger_succeeded_ops(factory, run_id),
        timeout=40,
    ), "planned odoo effects did not succeed through the fake adapter"
    with factory() as db:
        po = (
            db.execute(
                select(ProcurementOrder).where(ProcurementOrder.sku == "SKU-DBOS-3")
            )
            .scalars()
            .one()
        )
        entries = {
            entry.operation: entry
            for entry in db.execute(
                select(EffectLedgerEntry).where(
                    EffectLedgerEntry.approval_ref == run_id
                )
            )
            .scalars()
            .all()
        }
    assert set(entries) == {"po_create", "po_confirm"}
    assert entries["po_create"].status == EffectStatus.SUCCEEDED
    assert entries["po_create"].remote_reference == po.odoo_po_id
    assert entries["po_confirm"].status == EffectStatus.SUCCEEDED
    assert po.status == ProcurementStatus.PO_CONFIRMED
    odoo_fake = dbos_env["odoo_fake"]
    assert odoo_fake.calls.get("create_po") == 1
    assert odoo_fake.calls.get("confirm_po") == 1


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


class _ScriptedOdoo:
    """Odoo adapter fake: per-method scripted outcomes, succeed by default.

    Mirrors the real ``OdooConnector`` operation methods (the ``_OP_METHOD``
    dispatch set in ``app.services.effect_ledger``) so the DBOS effect step
    never reaches the real Odoo service.  Unscripted methods return
    :class:`EffectResult.succeeded` with a deterministic integer remote
    reference (Odoo record ids are integers) so ``finalize_after_effect`` can
    write back ``odoo_po_id`` / ``odoo_bill_id`` / ... and downstream
    validate effects can be built from them.  A method's script may raise
    ``OutcomeUnknownError`` / ``RetryableEffectError`` or return an
    ``EffectResult`` to shape the outcome per test.
    """

    name = "odoo"

    def __init__(self, script: dict[str, list] | None = None) -> None:
        self._script = {
            method: list(outcomes) for method, outcomes in (script or {}).items()
        }
        self.calls: dict[str, int] = {}
        self._remote_id = 1000

    def _next(self, method: str, kwargs: dict) -> EffectResult:
        self.calls[method] = self.calls.get(method, 0) + 1
        outcomes = self._script.get(method)
        if outcomes:
            outcome = outcomes[min(self.calls[method] - 1, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        odoo_id = kwargs.get("odoo_id")
        if odoo_id is not None:
            # The real connector reports the same record id back for
            # confirm/validate/write-by-id operations; echo it so finalize's
            # write-back of the domain column stays idempotent.
            return EffectResult.succeeded(
                str(odoo_id), f"hash:{method}:{self.calls[method]}"
            )
        self._remote_id += 1
        return EffectResult.succeeded(
            str(self._remote_id), f"hash:{method}:{self.calls[method]}"
        )

    def create_po(self, **kwargs):
        return self._next("create_po", kwargs)

    def confirm_po(self, **kwargs):
        return self._next("confirm_po", kwargs)

    def receive_transfer(self, **kwargs):
        return self._next("receive_transfer", kwargs)

    def create_bill(self, **kwargs):
        return self._next("create_bill", kwargs)

    def create_sale_order(self, **kwargs):
        return self._next("create_sale_order", kwargs)

    def confirm_sale_order(self, **kwargs):
        return self._next("confirm_sale_order", kwargs)

    def create_picking(self, **kwargs):
        return self._next("create_picking", kwargs)

    def validate_picking(self, **kwargs):
        return self._next("validate_picking", kwargs)

    def create_invoice(self, **kwargs):
        return self._next("create_invoice", kwargs)

    def validate_invoice(self, **kwargs):
        return self._next("validate_invoice", kwargs)

    def create_credit_note(self, **kwargs):
        return self._next("create_credit_note", kwargs)

    def validate_credit_note(self, **kwargs):
        return self._next("validate_credit_note", kwargs)

    def create_product(self, **kwargs):
        return self._next("create_product", kwargs)

    def update_product(self, **kwargs):
        return self._next("update_product", kwargs)

    def create_stock_move(self, **kwargs):
        return self._next("create_stock_move", kwargs)


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

    # The recovered workflow executed the planned Odoo effects through the
    # injected fake adapter (same isolation as the decision-relay test), so
    # the run stays awaiting_approval at the receipt gate instead of
    # settling into needs_reconciliation.
    assert _wait_for(
        lambda: {"po_create", "po_confirm"} <= _ledger_succeeded_ops(factory, run_id),
        timeout=60,
    ), "recovered workflow did not execute the planned odoo effects"
    with factory() as db:
        po = (
            db.execute(
                select(ProcurementOrder).where(ProcurementOrder.sku == "SKU-DBOS-K")
            )
            .scalars()
            .one()
        )
        assert po.status == ProcurementStatus.PO_CONFIRMED
        assert po.odoo_po_id is not None


__all__ = ["DBOS_IMPORTABLE", "PG_REACHABLE", "dbos_env"]
