from __future__ import annotations

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

    from app.models.effect import EffectLedgerEntry, EffectStatus
    from app.models.reconciliation import (
        ReconciliationDiff,
        ReconciliationDiffStatus,
        ReconciliationRun,
        ReconciliationRunStatus,
    )

    engine = sa.create_engine(url)
    with Session(engine) as db:
        effect = EffectLedgerEntry(
            intent_id=uuid.uuid4(),
            target_system="shopify",
            operation="product_publish",
            status=EffectStatus.SUCCEEDED,
        )
        run = ReconciliationRun(
            run_type="m2-migration",
            status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS,
        )
        db.add_all([effect, run])
        db.flush()
        db.add(
            ReconciliationDiff(
                run_id=run.id,
                domain="effect",
                entity_type="effect",
                entity_id=str(effect.intent_id),
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
