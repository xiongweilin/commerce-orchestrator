"""WP1 acceptance: Alembic 0001-0005 migrations on SQLite + model consistency.

Owned by the WP1 (schema/migrations) work package.  Runs the real migration
scripts end-to-end against a scratch SQLite file (``env.py`` is pointed at the
scratch URL by monkeypatching ``app.config.get_settings``; PG-only types are
patched to compile on SQLite), then verifies:

- upgrade/downgrade round-trips for every revision,
- the migrated schema matches ``app.models`` metadata (tables, columns,
  nullability, type categories, PK/unique/index/FK names),
- migration 0003 backfills ``orchestration_engine = 'legacy_inline'``,
- migration 0004 refuses to proceed on duplicate decisions and normalizes the
  idempotency status vocabulary to ``processing | completed``.

These tests never touch the real PostgreSQL database.
"""

from __future__ import annotations

import datetime as dt
import decimal
import types
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# SQLite compilation patches for PG-only types used by the migrations.
# Production (PostgreSQL) compilation is untouched.
# ---------------------------------------------------------------------------
@compiles(postgresql.UUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "CHAR(36)"


@compiles(postgresql.JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


@compiles(postgresql.BYTEA, "sqlite")
def _compile_bytea_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "BLOB"


def _make_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


@pytest.fixture
def migrate_url(tmp_path: Path) -> str:
    # Forward slashes are required for Windows sqlite URLs.
    return f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"


@pytest.fixture
def alembic_to_scratch(migrate_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Point alembic env.py at the scratch sqlite file for this test."""
    import app.config

    monkeypatch.setattr(
        app.config,
        "get_settings",
        lambda: types.SimpleNamespace(database_url=migrate_url),
    )
    return _make_config()


def _connect(url: str) -> sa.Engine:
    return sa.create_engine(url)


def _inspect(url: str) -> tuple[sa.Inspector, sa.Engine]:
    engine = _connect(url)
    return sa.inspect(engine), engine


def _column_sets_from_models():
    from app.models import Base

    return {
        table.name: {(col.name, col.nullable) for col in table.columns}
        for table in Base.metadata.sorted_tables
    }


def _column_sets_from_db(inspector: sa.Inspector) -> dict[str, set[tuple[str, bool]]]:
    return {
        table: {(col["name"], col["nullable"]) for col in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }


def _type_category(sql_type) -> str:
    python_type = sql_type.python_type
    if python_type is dict:
        return "json"
    if python_type is str:
        return "text"
    if python_type is int:
        return "int"
    if python_type is dt.datetime:
        return "datetime"
    if python_type is bool:
        return "bool"
    if python_type is decimal.Decimal:
        return "numeric"
    if python_type is bytes:
        return "blob"
    return f"other:{python_type}"


def _columns_compatible(model_type, db_type) -> bool:
    """Coarse cross-dialect type check without false positives.

    Model UUID columns are stored as CHAR(36) on the SQLite test database, so a
    model ``Uuid`` is accepted against any reflected string type of length >= 32.
    Everything else must share the same python_type category.
    """
    if isinstance(model_type, sa.Uuid):
        return _type_category(db_type) == "text" and getattr(db_type, "length", 0) >= 32
    if isinstance(model_type, sa.Enum):
        # native_enum=False -> VARCHAR storage; the python_type is the enum class.
        return _type_category(db_type) == "text"
    return _type_category(model_type) == _type_category(db_type)


def _model_table_shape():
    from app.models import Base

    shape: dict[str, dict] = {}
    for table in Base.metadata.sorted_tables:
        shape[table.name] = {
            "columns": {
                col.name: {
                    "nullable": col.nullable,
                    "type": col.type,
                }
                for col in table.columns
            },
            "pk": next(
                (
                    c.name
                    for c in table.constraints
                    if isinstance(c, sa.PrimaryKeyConstraint)
                ),
                None,
            ),
            "unique": sorted(
                {
                    c.name
                    for c in table.constraints
                    if isinstance(c, sa.UniqueConstraint) and c.name
                }
            ),
            "fk": sorted(
                {
                    c.name
                    for c in table.constraints
                    if isinstance(c, sa.ForeignKeyConstraint) and c.name
                }
            ),
            "index": sorted({ix.name for ix in table.indexes}),
        }
    return shape


def _db_table_shape(inspector: sa.Inspector) -> dict[str, dict]:
    shape: dict[str, dict] = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        shape[table] = {
            "columns": {
                col["name"]: {"nullable": col["nullable"], "type": col["type"]}
                for col in inspector.get_columns(table)
            },
            "pk": inspector.get_pk_constraint(table).get("name"),
            "unique": sorted(
                {
                    c["name"]
                    for c in inspector.get_unique_constraints(table)
                    if c.get("name")
                }
            ),
            "fk": sorted(
                {
                    c["name"]
                    for c in inspector.get_foreign_keys(table)
                    if c.get("name")
                }
            ),
            "index": sorted(
                {
                    ix["name"]
                    for ix in inspector.get_indexes(table)
                    if ix.get("name")
                }
            ),
        }
    return shape


def test_upgrade_round_trip_all_revisions(alembic_to_scratch: Config, migrate_url: str):
    """0001 -> head -> base -> head must succeed and recreate the full schema."""
    command.upgrade(alembic_to_scratch, "head")

    inspector, engine = _inspect(migrate_url)
    try:
        tables = set(inspector.get_table_names())
        assert "runtime_heartbeat" in tables
        assert "sensitive_payload" in tables
        assert "workflow_run" in tables
    finally:
        engine.dispose()

    command.downgrade(alembic_to_scratch, "base")
    inspector, engine = _inspect(migrate_url)
    try:
        assert set(inspector.get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()

    command.upgrade(alembic_to_scratch, "head")
    inspector, engine = _inspect(migrate_url)
    try:
        assert "runtime_heartbeat" in inspector.get_table_names()
        assert "sensitive_payload" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_downgrade_0003_removes_new_columns(alembic_to_scratch: Config, migrate_url: str):
    """Downgrade to 0002 must remove every column/table added by 0003-0005."""
    command.upgrade(alembic_to_scratch, "head")
    command.downgrade(alembic_to_scratch, "0002_projection_table")

    inspector, engine = _inspect(migrate_url)
    try:
        tables = set(inspector.get_table_names())
        assert "runtime_heartbeat" not in tables
        assert "sensitive_payload" not in tables
        workflow_cols = {c["name"] for c in inspector.get_columns("workflow_run")}
        assert "orchestration_engine" not in workflow_cols
        assert "dbos_workflow_id" not in workflow_cols
        assert "initiated_by_user_id" not in workflow_cols
        assert "started_at" not in workflow_cols
        assert "finished_at" not in workflow_cols
        work_item_cols = {c["name"] for c in inspector.get_columns("work_item")}
        assert "proposed_by_user_id" not in work_item_cols
        inbox_cols = {c["name"] for c in inspector.get_columns("inbox_event")}
        assert "lease_until" not in inbox_cols
        assert "attempts" not in inbox_cols
        assert "processed_at" not in inbox_cols
        idem_cols = {c["name"] for c in inspector.get_columns("idempotency_record")}
        assert "updated_at" not in idem_cols
    finally:
        engine.dispose()


def test_models_match_migrated_schema(alembic_to_scratch: Config, migrate_url: str):
    """The migrated schema and app.models metadata agree on the P7 checklist."""
    command.upgrade(alembic_to_scratch, "head")

    inspector, engine = _inspect(migrate_url)
    try:
        model_shape = _model_table_shape()
        db_shape = _db_table_shape(inspector)

        assert set(model_shape) == set(db_shape), (
            f"table sets differ: only in models={set(model_shape) - set(db_shape)} "
            f"only in db={set(db_shape) - set(model_shape)}"
        )

        for table, model in model_shape.items():
            db = db_shape[table]
            assert model["pk"] == db["pk"], f"{table}: pk name mismatch"
            assert model["unique"] == db["unique"], f"{table}: unique names mismatch"
            assert model["fk"] == db["fk"], f"{table}: fk names mismatch"
            assert model["index"] == db["index"], f"{table}: index names mismatch"
            assert set(model["columns"]) == set(db["columns"]), (
                f"{table}: column names mismatch "
                f"(only in model={set(model['columns']) - set(db['columns'])}, "
                f"only in db={set(db['columns']) - set(model['columns'])})"
            )
            for col, spec in model["columns"].items():
                db_col = db["columns"][col]
                assert spec["nullable"] == db_col["nullable"], (
                    f"{table}.{col}: nullability mismatch"
                )
                assert _columns_compatible(spec["type"], db_col["type"]), (
                    f"{table}.{col}: type mismatch "
                    f"(model={_type_category(spec['type'])}, "
                    f"db={_type_category(db_col['type'])})"
                )
    finally:
        engine.dispose()


def test_0003_backfills_legacy_inline(alembic_to_scratch: Config, migrate_url: str):
    """Existing workflow rows get orchestration_engine='legacy_inline'."""
    command.upgrade(alembic_to_scratch, "0002_projection_table")

    engine = _connect(migrate_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_run "
                    "(id, workflow_type, workflow_version, status, version, "
                    " created_at, updated_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000101', 'test-flow', "
                    "1, 'accepted', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(alembic_to_scratch, "head")

    engine = _connect(migrate_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT orchestration_engine FROM workflow_run "
                    "WHERE id = '00000000-0000-0000-0000-000000000101'"
                )
            ).one()
        assert row[0] == "legacy_inline"
    finally:
        engine.dispose()


def test_0004_duplicate_decision_precheck_blocks(
    alembic_to_scratch: Config, migrate_url: str
):
    """Duplicate decisions abort migration 0004 with ids (no payload)."""
    command.upgrade(alembic_to_scratch, "0003_runtime_orchestration")

    engine = _connect(migrate_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_run "
                    "(id, workflow_type, workflow_version, status, version, "
                    " created_at, updated_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000201', 'test-flow', "
                    "1, 'accepted', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO work_item "
                    "(id, workflow_id, kind, title, status, version, "
                    " created_at, updated_at) "
                    "VALUES ('00000000-0000-0000-0000-000000000202', "
                    "'00000000-0000-0000-0000-000000000201', 'approval', "
                    "'Approve?', 'pending', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for decision_id in ("203", "204"):
                conn.execute(
                    sa.text(
                        "INSERT INTO work_item_decision "
                        "(id, work_item_id, decision, user_id, submitted_version, "
                        " created_at) "
                        "VALUES (:id, "
                        "'00000000-0000-0000-0000-000000000202', 'approve', "
                        "'00000000-0000-0000-0000-000000000205', 1, CURRENT_TIMESTAMP)"
                    ),
                    {"id": f"00000000-0000-0000-0000-000000000{decision_id}"},
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(alembic_to_scratch, "0004_concurrency_constraints")
    message = str(exc_info.value)
    assert "1 work item(s)" in message
    assert "00000000-0000-0000-0000-000000000202" in message


def test_0004_normalizes_idempotency_status(
    alembic_to_scratch: Config, migrate_url: str
):
    """Legacy idempotency statuses 'pending'/'done' become processing/completed."""
    command.upgrade(alembic_to_scratch, "0003_runtime_orchestration")

    engine = _connect(migrate_url)
    try:
        with engine.begin() as conn:
            rows_to_insert = (
                ("00000000-0000-0000-0000-000000000301", "k-pending", "pending"),
                ("00000000-0000-0000-0000-000000000302", "k-done", "done"),
            )
            for row_id, key, status in rows_to_insert:
                conn.execute(
                    sa.text(
                        "INSERT INTO idempotency_record "
                        "(id, scope, key, request_hash, status, created_at) "
                        "VALUES (:id, 'test', :key, 'hash', :status, "
                        "CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": row_id,
                        "key": key,
                        "status": status,
                    },
                )
    finally:
        engine.dispose()

    command.upgrade(alembic_to_scratch, "head")

    engine = _connect(migrate_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT key, status, updated_at FROM idempotency_record "
                    "ORDER BY key"
                )
            ).all()
        by_key = {row[0]: row[1] for row in rows}
        assert by_key == {"k-done": "completed", "k-pending": "processing"}
        assert all(row[2] is not None for row in rows), "updated_at must be backfilled"
    finally:
        engine.dispose()


def test_model_field_checklist_matches_plan():
    """Field names from the WP1 brief exist on the models, verbatim."""
    from app.models.messaging import IdempotencyRecord, InboxEvent, InboxStatus
    from app.models.runtime import RuntimeHeartbeat
    from app.models.sensitive_payload import SensitivePayload
    from app.models.workflow import (
        WorkflowRun,
        WorkflowRunStatus,
        WorkItem,
        WorkItemDecision,
    )

    workflow_cols = {c.name for c in WorkflowRun.__table__.columns}
    assert {"orchestration_engine", "dbos_workflow_id", "initiated_by_user_id",
            "started_at", "finished_at"} <= workflow_cols
    assert WorkflowRunStatus.NEEDS_RECONCILIATION.value == "needs_reconciliation"

    assert "proposed_by_user_id" in {c.name for c in WorkItem.__table__.columns}
    assert WorkItemDecision.__table__.constraints, "work item decision must stay constrained"
    assert any(
        isinstance(c, sa.UniqueConstraint)
        and c.name == "uq_work_item_decision_work_item_id"
        for c in WorkItemDecision.__table__.constraints
    )

    inbox_cols = {c.name for c in InboxEvent.__table__.columns}
    assert {"attempts", "next_attempt_at", "lease_until", "last_error",
            "processed_at"} <= inbox_cols
    assert InboxStatus.PROCESSING.value == "processing"

    idem_cols = {c.name for c in IdempotencyRecord.__table__.columns}
    assert "updated_at" in idem_cols
    assert IdempotencyRecord.status.default.arg == "processing"

    heartbeat_cols = {c.name for c in RuntimeHeartbeat.__table__.columns}
    assert {"process_name", "instance_id", "status", "started_at", "heartbeat_at",
            "details_json"} <= heartbeat_cols

    payload_cols = {c.name for c in SensitivePayload.__table__.columns}
    assert {"purpose", "classification", "owner", "source_type", "source_id",
            "ciphertext", "key_version", "expires_at", "deleted_at",
            "created_at"} <= payload_cols
