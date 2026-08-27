from __future__ import annotations

import datetime as dt
import types
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@compiles(postgresql.UUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "CHAR(36)"


@compiles(postgresql.JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "JSON"


@compiles(postgresql.BYTEA, "sqlite")
def _compile_bytea_sqlite(type_, compiler, **kw):  # noqa: ARG001
    return "BLOB"


def _config(url: str, monkeypatch) -> Config:
    import app.config

    monkeypatch.setattr(
        app.config,
        "get_settings",
        lambda: types.SimpleNamespace(database_url=url),
    )
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_0008_does_not_backfill_historical_lifecycle_states(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'm2-pre0008.db').as_posix()}"
    cfg = _config(url, monkeypatch)
    command.upgrade(cfg, "0007_publication_qualification")

    from app.models.reconciliation import (
        ReconciliationDiff,
        ReconciliationDiffStatus,
        ReconciliationRun,
        ReconciliationRunStatus,
    )

    engine = sa.create_engine(url)
    intent_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)

    # This fixture intentionally writes against the 0007 schema. Using the
    # current EffectLedgerEntry ORM here would leak future 0009 columns into an
    # insert that is specifically meant to represent a historical row.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO effect_ledger_entry "
                "(id, intent_id, target_system, operation, attempt, status, version, "
                "created_at, updated_at) "
                "VALUES (:id, :intent_id, :target_system, :operation, :attempt, :status, "
                ":version, :created_at, :updated_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "intent_id": str(intent_id),
                "target_system": "shopify",
                "operation": "product_publish",
                "attempt": 0,
                "status": "succeeded",
                "version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )

    with Session(engine) as db:
        run = ReconciliationRun(
            run_type="m2-migration",
            status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS,
        )
        db.add(run)
        db.flush()
        db.add(
            ReconciliationDiff(
                run_id=run.id,
                domain="effect",
                entity_type="effect",
                entity_id=str(intent_id),
                status=ReconciliationDiffStatus.RESOLVED,
            )
        )
        db.commit()

    command.upgrade(cfg, "0008_realization_resolution")
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM effect_realization_assessment")).scalar_one()
            == 0
        )
        assert (
            conn.execute(sa.text("SELECT count(*) FROM reconciliation_resolution")).scalar_one()
            == 0
        )
    engine.dispose()
