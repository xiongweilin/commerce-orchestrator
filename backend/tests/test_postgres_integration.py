"""PostgreSQL integration tests (P7 WP7, 整改计划 §六.1).

Every test here requires a reachable local PostgreSQL (the Docker
``commerce-postgres`` container with the documented local dev credentials);
the whole module skips when the database is unreachable.  All tests run in an
isolated temporary database created per module — business data is never
touched and the database is dropped at teardown.

Covered scenarios:

- empty database -> full Alembic migration chain to head;
- ``0002``-era fixture data upgraded through 0003/0004/0005 (backfill +
  unique-constraint + vocabulary normalization);
- 20 concurrent identical commands create exactly one workflow;
- concurrent same-key/different-body writes: one wins, the rest fail;
- two concurrent approvals on one work item: exactly one succeeds;
- inbox ``FOR UPDATE SKIP LOCKED`` claim, lease expiry recovery, exponential
  backoff and dead-lettering;
- retention cleanup with tombstones and marker referential integrity.
"""

from __future__ import annotations

import os
import secrets
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utc_now
from app.core.uuid7 import uuid7
from app.models.identity import Role, RoleAssignment, User
from app.models.messaging import IdempotencyRecord, InboxEvent, InboxStatus
from app.models.order import SalesOrder
from app.models.sensitive_payload import SensitivePayload
from app.models.workflow import WorkflowRun, WorkItem, WorkItemDecision
from app.services.approvals import submit_decision
from app.services.commands import accept_command, dispatch_command
from app.services.outbox_inbox import (
    claim_inbox_batch,
    emit_event,
    exponential_backoff_seconds,
    recover_expired_leases,
    relay_inbox_batch,
)
from app.services.privacy import (
    backfill_customer_refs,
    cleanup_expired_payloads,
    store_sensitive_payload,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = str(BACKEND_ROOT / "alembic.ini")
ALEMBIC_SCRIPT = str(BACKEND_ROOT / "alembic")

# Documented local dev credentials (infra/postgres/init.sql + config defaults).
PG_ADMIN_URL = "postgresql://commerce:commerce@localhost:5432/postgres"


def _pg_reachable() -> bool:
    try:
        with psycopg.connect(PG_ADMIN_URL, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - skip decision, not a test failure
        return False


PG_REACHABLE = _pg_reachable()


def _create_database(name: str) -> None:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}" OWNER commerce')


def _drop_database(name: str) -> None:
    with psycopg.connect(PG_ADMIN_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _run_alembic(url: str, revision: str) -> None:
    """Run alembic in-process against ``url``.

    ``alembic/env.py`` reads the URL from the cached settings; clear the cache
    after setting the env var and restore it afterwards so the shared sqlite
    test settings are untouched for the rest of the suite.
    """
    from alembic.config import Config

    from alembic import command
    from app.config import get_settings

    previous = os.environ.get("COMMERCE_DATABASE_URL")
    os.environ["COMMERCE_DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        cfg = Config(ALEMBIC_INI)
        cfg.set_main_option("script_location", ALEMBIC_SCRIPT)
        command.upgrade(cfg, revision)
    finally:
        if previous is None:
            os.environ.pop("COMMERCE_DATABASE_URL", None)
        else:
            os.environ["COMMERCE_DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def pg_factory() -> Iterator[Callable[[], Session]]:
    """A session factory bound to a fresh, fully-migrated temp database.

    Function-scoped: every test gets its own database so concurrency tests and
    row-count assertions are never polluted by a previous test's data.
    """
    if not PG_REACHABLE:
        pytest.skip("PostgreSQL unreachable (Docker commerce-postgres required)")
    db_name = f"commerce_p7_it_{secrets.token_hex(4)}"
    _create_database(db_name)
    url = f"postgresql+psycopg://commerce:commerce@localhost:5432/{db_name}"
    _run_alembic(url, "head")
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()
        _drop_database(db_name)


def _make_user(factory: Callable[[], Session], roles: list[str]) -> uuid.UUID:
    """Create a user with roles on PostgreSQL.

    User and RoleAssignment are committed separately: the models have no
    relationship, so SQLAlchemy orders the inserts by class name and a single
    flush would insert ``role_assignment`` before ``user`` (FK violation on
    PostgreSQL — see WP7-REPORT.md).
    """
    user_id = uuid7()
    with factory() as db:
        db.add(
            User(
                id=user_id,
                email=f"it-{user_id}@test.local",
                display_name="IT User",
            )
        )
        db.commit()
    with factory() as db:
        for role in roles:
            db.add(RoleAssignment(user_id=user_id, role=Role(role), scope="*"))
        db.commit()
    return user_id


# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------


def test_empty_database_migrates_to_head() -> None:
    if not PG_REACHABLE:
        pytest.skip("PostgreSQL unreachable")
    db_name = f"commerce_p7_mig_{secrets.token_hex(4)}"
    _create_database(db_name)
    url = f"postgresql+psycopg://commerce:commerce@localhost:5432/{db_name}"
    try:
        _run_alembic(url, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            }
        engine.dispose()
        assert head == "0006_remote_entity_ids"
        for expected in (
            "workflow_run",
            "work_item",
            "work_item_decision",
            "inbox_event",
            "outbox_event",
            "runtime_heartbeat",
            "sensitive_payload",
            "reconciliation_run",
        ):
            assert expected in tables, f"missing table {expected}"
    finally:
        _drop_database(db_name)


def test_0002_fixture_data_upgrades_through_head() -> None:
    """0002-era rows survive 0003-0005 and get backfilled/normalized."""
    if not PG_REACHABLE:
        pytest.skip("PostgreSQL unreachable")
    db_name = f"commerce_p7_0002_{secrets.token_hex(4)}"
    _create_database(db_name)
    url = f"postgresql+psycopg://commerce:commerce@localhost:5432/{db_name}"
    try:
        _run_alembic(url, "0002_projection_table")
        run_id = uuid.uuid4()
        item_id = uuid.uuid4()
        decision_id = uuid.uuid4()
        now = utc_now()
        with psycopg.connect(
            f"postgresql://commerce:commerce@localhost:5432/{db_name}"
        ) as conn, conn.cursor() as cur:
            # 0002-era workflow_run (0001 schema, no new columns).
            cur.execute(
                """
                    INSERT INTO workflow_run
                      (id, workflow_type, workflow_version, status, correlation_id,
                       input_json, result_json, error, version, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    run_id,
                    "procurement",
                    1,
                    "awaiting_approval",
                    "corr-0002",
                    '{"sku": "SKU-OLD"}',
                    None,
                    None,
                    1,
                    now,
                    now,
                ),
            )
            cur.execute(
                """
                    INSERT INTO work_item
                      (id, workflow_id, kind, title, required_roles, assignee_user_id,
                       payload_json, status, expected_version, decision_json,
                       decided_by_user_id, decided_at, expires_at, version,
                       created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    item_id,
                    run_id,
                    "approval",
                    "Approve PO",
                    '["budget_owner"]',
                    None,
                    '{"next_step": "approve_po"}',
                    "pending",
                    1,
                    None,
                    None,
                    None,
                    None,
                    1,
                    now,
                    now,
                ),
            )
            cur.execute(
                """
                    INSERT INTO work_item_decision
                      (id, work_item_id, decision, user_id, reason, submitted_version,
                       created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    decision_id,
                    item_id,
                    "approve",
                    uuid.uuid4(),
                    "historic",
                    1,
                    now,
                ),
            )
            # 0004 normalizes the legacy idempotency vocabulary
            # (done -> completed, pending -> processing).
            cur.execute(
                """
                    INSERT INTO idempotency_record
                      (id, scope, key, request_hash, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                (uuid.uuid4(), "legacy", "k-done", "h", "done", now),
            )
            cur.execute(
                """
                    INSERT INTO idempotency_record
                      (id, scope, key, request_hash, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                (uuid.uuid4(), "legacy", "k-pending", "h", "pending", now),
            )

        _run_alembic(url, "head")
        engine = create_engine(url)
        with engine.connect() as conn:
            run = conn.execute(
                text("SELECT workflow_type, orchestration_engine, status FROM workflow_run")
            ).one()
            decision_count = conn.execute(
                text("SELECT count(*) FROM work_item_decision")
            ).scalar_one()
            idem_values = set(
                conn.execute(
                    text("SELECT DISTINCT status FROM idempotency_record")
                ).scalars()
            )
        engine.dispose()
        # Row preserved; 0003 backfilled the engine to legacy_inline.
        assert run.workflow_type == "procurement"
        assert run.orchestration_engine == "legacy_inline"
        assert run.status == "awaiting_approval"
        assert decision_count == 1
        # 0004 normalized the legacy idempotency vocabulary.
        assert "done" not in idem_values
        assert "pending" not in idem_values
    finally:
        _drop_database(db_name)


# ---------------------------------------------------------------------------
# Concurrency: idempotent command acceptance (计划 §四.1)
# ---------------------------------------------------------------------------


def test_concurrent_20_same_command_creates_one_workflow(pg_factory) -> None:
    actor = _make_user(pg_factory, ["catalog_owner"])
    results: list[tuple[str, str]] = []
    barrier = threading.Barrier(20)
    lock = threading.Lock()

    def _worker() -> None:
        db = pg_factory()
        try:
            barrier.wait(timeout=30)
            accepted = accept_command(
                db,
                command={"type": "catalog-revision", "payload": {"sku": "SKU-CONC"}},
                actor_user_id=actor,
                idempotency_key="same-key-20",
                correlation_id="corr-conc",
            )
            db.commit()
            with lock:
                results.append(("ok", str(accepted.workflow_id)))
        except Exception as exc:  # noqa: BLE001 - losers raise; count them
            db.rollback()
            with lock:
                results.append(("err", type(exc).__name__))
        finally:
            db.close()

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    ok_ids = {r[1] for r in results if r[0] == "ok"}
    # Once the first acceptance commits, every later thread replays the same
    # original result (同 body、已完成：返回原结果); a thread that races the
    # insert instead sees the unique-constraint IntegrityError.  Either way
    # only one workflow may exist.
    assert len(ok_ids) <= 1, f"expected one workflow id, got {results}"
    with pg_factory() as db:
        runs = db.execute(select(func.count()).select_from(WorkflowRun)).scalar_one()
        idem = db.execute(select(func.count()).select_from(IdempotencyRecord)).scalar_one()
        accepted_events = db.execute(
            select(func.count())
            .select_from(InboxEvent)
            .where(InboxEvent.status == InboxStatus.PENDING)
        ).scalar_one()
    assert runs == 1
    assert idem == 1
    assert accepted_events == 1


def test_concurrent_same_key_different_body_only_one_wins(pg_factory) -> None:
    actor = _make_user(pg_factory, ["catalog_owner"])
    results: list[tuple[str, str]] = []
    barrier = threading.Barrier(10)
    lock = threading.Lock()

    def _worker(idx: int) -> None:
        db = pg_factory()
        try:
            barrier.wait(timeout=30)
            accept_command(
                db,
                command={
                    "type": "catalog-revision",
                    "payload": {"sku": f"SKU-BODY-{idx}"},
                },
                actor_user_id=actor,
                idempotency_key="same-key-diff-body",
                correlation_id="corr-conc2",
            )
            db.commit()
            with lock:
                results.append(("ok", str(idx)))
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            with lock:
                results.append(("err", type(exc).__name__))
        finally:
            db.close()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sum(1 for r in results if r[0] == "ok") == 1, results
    with pg_factory() as db:
        assert db.execute(select(func.count()).select_from(WorkflowRun)).scalar_one() == 1
        assert (
            db.execute(select(func.count()).select_from(IdempotencyRecord)).scalar_one() == 1
        )


def test_two_users_concurrent_approval_only_one_succeeds(pg_factory) -> None:
    proposer = _make_user(pg_factory, ["procurement_lead"])
    approver_a = _make_user(pg_factory, ["budget_owner"])
    approver_b = _make_user(pg_factory, ["budget_owner"])
    with pg_factory() as db:
        result = dispatch_command(
            db,
            scope=f"seed-{uuid.uuid4()}",
            key=f"key-{uuid.uuid4()}",
            command_type="procurement",
            payload={"sku": "SKU-APPROVE", "qty": "1", "supplier": "ACME", "unit_cost": "1"},
            actor_user_id=proposer,
        )
        item = (
            db.execute(
                select(WorkItem).where(
                    WorkItem.workflow_id == uuid.UUID(result["workflowId"])
                )
            )
            .scalars()
            .one()
        )
        db.commit()
        item_id = item.id
        expected_version = item.expected_version or 1

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _approve(user: uuid.UUID, key: str) -> None:
        db = pg_factory()
        try:
            barrier.wait(timeout=30)
            outcome = submit_decision(
                db,
                work_item_id=item_id,
                user_id=user,
                decision="approve",
                reason="concurrent",
                expected_workflow_version=expected_version,
                idempotency_key=key,
            )
            db.commit()
            with lock:
                outcomes.append(f"ok:{outcome['status']}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            with lock:
                outcomes.append(f"err:{type(exc).__name__}")
        finally:
            db.close()

    ta = threading.Thread(target=_approve, args=(approver_a, "dec-a"))
    tb = threading.Thread(target=_approve, args=(approver_b, "dec-b"))
    ta.start()
    tb.start()
    ta.join(timeout=60)
    tb.join(timeout=60)

    assert sum(1 for o in outcomes if o.startswith("ok:")) == 1, outcomes
    assert any(o == "err:ConflictError" for o in outcomes), outcomes
    with pg_factory() as db:
        assert (
            db.execute(select(func.count()).select_from(WorkItemDecision)).scalar_one() == 1
        )
        item = db.get(WorkItem, item_id)
        assert item.status.value == "approved"


# ---------------------------------------------------------------------------
# Inbox relay on PostgreSQL (SKIP LOCKED, lease, backoff, dead-letter)
# ---------------------------------------------------------------------------


def test_inbox_skip_locked_claims_disjoint_batches(pg_factory) -> None:
    with pg_factory() as db:
        for i in range(5):
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

    with pg_factory() as db:
        first = claim_inbox_batch(db, consumer="worker", batch=2, lease_seconds=30)
        first_ids = {row.event_id for row in first}
        db.commit()
    with pg_factory() as db:
        # The first claim's rows are locked (SKIP LOCKED): the second claim
        # must skip them even though they are still processing.
        second = claim_inbox_batch(db, consumer="worker", batch=3, lease_seconds=30)
        second_ids = {row.event_id for row in second}
        db.commit()
        assert len(first_ids) == 2
        assert len(second_ids) == 3
        assert first_ids.isdisjoint(second_ids)
        rows = db.execute(select(InboxEvent)).scalars().all()
        assert all(row.status == InboxStatus.PROCESSING for row in rows)
        assert all(row.lease_until is not None for row in rows)


def test_inbox_lease_expiry_recovery_and_backoff_dead_letter(pg_factory) -> None:
    with pg_factory() as db:
        emit_event(
            db,
            event_type="workflow.accepted",
            aggregate_type="workflow",
            aggregate_id="w-lease",
            producer="workflow",
            payload={"workflow_id": "w-lease"},
            consumers=["worker"],
        )
        lease_row = db.execute(select(InboxEvent)).scalar_one()
        lease_row.status = InboxStatus.PROCESSING
        lease_row.lease_until = utc_now() - timedelta(seconds=1)
        db.commit()
        recovered = recover_expired_leases(db, consumer="worker")
        db.commit()
        assert recovered == 1
        db.refresh(lease_row)
        assert lease_row.status == InboxStatus.PENDING
        assert lease_row.lease_until is None
        # Retire the recovered row so the backoff scenario below only claims
        # its own event.
        lease_row.status = InboxStatus.PROCESSED
        db.commit()

    # Backoff + dead-letter: a dispatch that always fails retries with
    # exponential backoff and dead-letters at max_attempts.
    with pg_factory() as db:
        emit_event(
            db,
            event_type="workflow.accepted",
            aggregate_type="workflow",
            aggregate_id="w-backoff",
            producer="workflow",
            payload={"workflow_id": "w-backoff"},
            consumers=["worker"],
        )
        db.commit()
        row_id = (
            db.execute(
                select(InboxEvent.id).where(
                    InboxEvent.event_id == _event_id_of(db, "w-backoff")
                )
            )
            .scalars()
            .one()
        )

        def _boom(_event) -> None:
            raise RuntimeError("dispatch failed")

        stats = relay_inbox_batch(
            db,
            consumer="worker",
            dispatch=_boom,
            max_attempts=2,
        )
        db.commit()
        assert stats.retried == 1
        row = db.get(InboxEvent, row_id)
        assert row.status == InboxStatus.PENDING
        assert row.attempts == 1
        assert row.next_attempt_at is not None
        assert row.next_attempt_at > utc_now()
        # The backoff window has elapsed: make the row claimable again.
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
        db.commit()

        stats2 = relay_inbox_batch(
            db,
            consumer="worker",
            dispatch=_boom,
            max_attempts=2,
        )
        db.commit()
        assert stats2.dead_lettered == 1
        row = db.get(InboxEvent, row_id)
        assert row.status == InboxStatus.FAILED
        assert row.attempts == 2
        assert row.last_error is not None
        assert exponential_backoff_seconds(1) == 1.0
        assert exponential_backoff_seconds(2) == 2.0


def _event_id_of(db, aggregate_id: str) -> uuid.UUID:
    from app.models.messaging import OutboxEvent

    return (
        db.execute(
            select(OutboxEvent.event_id).where(OutboxEvent.aggregate_id == aggregate_id)
        )
        .scalars()
        .one()
    )


# ---------------------------------------------------------------------------
# Privacy retention cleanup (计划 §五.3)
# ---------------------------------------------------------------------------


def test_cleanup_expiry_and_marker_referential_integrity(pg_factory, monkeypatch) -> None:
    from app.services import privacy

    monkeypatch.setattr(privacy, "_pii_hash_key", lambda: b"it-hash-key")
    with pg_factory() as db:
        order = SalesOrder(
            order_ref="SO-IT-1",
            shopify_order_id="gid://shopify/Order/1",
            customer_ref="customer@example.com",
            status="received",
            currency="CNY",
            total="99.00",
            shipping={"address1": "Secret St", "city": "Shanghai"},
        )
        db.add(order)
        db.commit()

        stats = backfill_customer_refs(db)
        db.commit()
        assert stats.total == 2  # customer_ref + shipping
        db.refresh(order)
        assert privacy.is_pseudonymous(order.customer_ref)
        marker_id = order.shipping["sensitivePayloadId"]

        # One payload expires immediately, the fresh ones stay.
        expired = store_sensitive_payload(
            db,
            purpose="customer_ref",
            classification="PII",
            owner="commerce",
            source_type="sales_order",
            source_id=order.id,
            plaintext="expired@example.com",
            expires_days=0,
        )
        db.commit()

        cleanup = cleanup_expired_payloads(db, now=utc_now() + timedelta(hours=1))
        db.commit()
        assert cleanup.cleared == 1

        db.refresh(expired)
        assert expired.ciphertext == ""
        assert expired.deleted_at is not None

        vault = db.execute(select(SensitivePayload)).scalars().all()
        assert len(vault) == 3
        # Marker referential integrity: the business row still references an
        # existing (tombstoned or live) vault row.
        assert any(str(row.id) == marker_id for row in vault)
        assert all(row.ciphertext or row.deleted_at is not None for row in vault)


__all__ = ["PG_REACHABLE", "pg_factory"]
