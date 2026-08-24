from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def write(path: str, content: str) -> None:
    Path(path).write_text(dedent(content).lstrip(), encoding="utf-8")


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing M2 anchor in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


def main() -> None:
    write(
        "app/models/effect_realization.py",
        """
        \"\"\"Append-only assessments of whether an external effect was realized.\"\"\"

        from __future__ import annotations

        import datetime as dt
        import enum
        import uuid

        from sqlalchemy import JSON, DateTime, Enum, ForeignKey
        from sqlalchemy.orm import Mapped, mapped_column

        from app.core.time import utc_now
        from app.models.base import Base, UUIDPkMixin, enum_values


        class EffectRealizationStatus(enum.StrEnum):
            UNVERIFIED = "unverified"
            VERIFIED = "verified"
            FAILED = "failed"
            UNKNOWN = "unknown"


        class EffectRealizationAssessment(UUIDPkMixin, Base):
            \"\"\"Immutable reality judgment about one effect-ledger entry.\"\"\"

            __tablename__ = "effect_realization_assessment"

            effect_id: Mapped[uuid.UUID] = mapped_column(
                ForeignKey("effect_ledger_entry.id"), nullable=False, index=True
            )
            realization_status: Mapped[EffectRealizationStatus] = mapped_column(
                Enum(
                    EffectRealizationStatus,
                    native_enum=False,
                    length=16,
                    values_callable=enum_values,
                ),
                nullable=False,
            )
            evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
            assessed_by_user_id: Mapped[uuid.UUID] = mapped_column(
                ForeignKey("user.id"), nullable=False
            )
            assessed_at: Mapped[dt.datetime] = mapped_column(
                DateTime(timezone=True), nullable=False, default=utc_now
            )


        __all__ = ["EffectRealizationAssessment", "EffectRealizationStatus"]
        """,
    )

    write(
        "app/models/reconciliation_resolution.py",
        """
        \"\"\"Append-only reconciliation disposition and verification records.\"\"\"

        from __future__ import annotations

        import datetime as dt
        import enum
        import uuid

        from sqlalchemy import JSON, DateTime, Enum, ForeignKey
        from sqlalchemy.orm import Mapped, mapped_column

        from app.core.time import utc_now
        from app.models.base import Base, UUIDPkMixin, enum_values


        class ReconciliationResolutionKind(enum.StrEnum):
            REPAIRED = "repaired"
            ACCEPTED_DIFFERENCE = "accepted_difference"
            AUTHORITATIVE_EXTERNAL = "authoritative_external"
            AUTHORITATIVE_INTERNAL = "authoritative_internal"
            SUPERSEDED = "superseded"
            FALSE_POSITIVE = "false_positive"


        class ReconciliationVerificationStatus(enum.StrEnum):
            UNVERIFIED = "unverified"
            VERIFIED = "verified"
            FAILED = "failed"
            UNKNOWN = "unknown"


        class ReconciliationResolution(UUIDPkMixin, Base):
            \"\"\"Immutable disposition plus an independent verification judgment.\"\"\"

            __tablename__ = "reconciliation_resolution"

            reconciliation_diff_id: Mapped[uuid.UUID] = mapped_column(
                ForeignKey("reconciliation_diff.id"), nullable=False, index=True
            )
            resolution_kind: Mapped[ReconciliationResolutionKind] = mapped_column(
                Enum(
                    ReconciliationResolutionKind,
                    native_enum=False,
                    length=32,
                    values_callable=enum_values,
                ),
                nullable=False,
            )
            verification_status: Mapped[ReconciliationVerificationStatus] = mapped_column(
                Enum(
                    ReconciliationVerificationStatus,
                    native_enum=False,
                    length=16,
                    values_callable=enum_values,
                ),
                nullable=False,
            )
            basis_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
            evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
            recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
                ForeignKey("user.id"), nullable=False
            )
            recorded_at: Mapped[dt.datetime] = mapped_column(
                DateTime(timezone=True), nullable=False, default=utc_now
            )


        __all__ = [
            "ReconciliationResolution",
            "ReconciliationResolutionKind",
            "ReconciliationVerificationStatus",
        ]
        """,
    )

    write(
        "app/services/realization_resolution.py",
        """
        \"\"\"Append-only realization and reconciliation semantic records.\"\"\"

        from __future__ import annotations

        import uuid
        from collections.abc import Iterable

        from sqlalchemy import select

        from app.core.errors import NotFoundError, ValidationError
        from app.models.effect import EffectLedgerEntry
        from app.models.effect_realization import (
            EffectRealizationAssessment,
            EffectRealizationStatus,
        )
        from app.models.identity import User
        from app.models.reconciliation import ReconciliationDiff
        from app.models.reconciliation_resolution import (
            ReconciliationResolution,
            ReconciliationResolutionKind,
            ReconciliationVerificationStatus,
        )


        def _refs(values: Iterable[str]) -> list[str]:
            refs: list[str] = []
            seen: set[str] = set()
            for value in values:
                ref = str(value).strip()
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
            return refs


        def _require_actor(db, actor_user_id: uuid.UUID) -> uuid.UUID:
            if db.get(User, actor_user_id) is None:
                raise NotFoundError("semantic judgment actor not found")
            return actor_user_id


        def append_effect_realization_assessment(
            db,
            *,
            effect_id: uuid.UUID,
            realization_status: EffectRealizationStatus | str,
            assessed_by_user_id: uuid.UUID,
            evidence_refs: Iterable[str] = (),
        ) -> EffectRealizationAssessment:
            \"\"\"Append reality judgment without rewriting execution history.\"\"\"
            effect = db.get(EffectLedgerEntry, effect_id)
            if effect is None:
                raise NotFoundError("effect ledger entry not found")
            status = EffectRealizationStatus(realization_status)
            evidence = _refs(evidence_refs)
            if status is not EffectRealizationStatus.UNVERIFIED and not evidence:
                raise ValidationError(
                    f"{status.value} effect realization assessment requires evidence_refs"
                )
            assessment = EffectRealizationAssessment(
                effect_id=effect.id,
                realization_status=status,
                evidence_refs=evidence,
                assessed_by_user_id=_require_actor(db, assessed_by_user_id),
            )
            db.add(assessment)
            db.flush()
            return assessment


        def latest_effect_realization_assessment(
            db, effect_id: uuid.UUID
        ) -> EffectRealizationAssessment | None:
            return (
                db.execute(
                    select(EffectRealizationAssessment)
                    .where(EffectRealizationAssessment.effect_id == effect_id)
                    .order_by(
                        EffectRealizationAssessment.assessed_at.desc(),
                        EffectRealizationAssessment.id.desc(),
                    )
                    .limit(1)
                )
                .scalars()
                .first()
            )


        def append_reconciliation_resolution(
            db,
            *,
            reconciliation_diff_id: uuid.UUID,
            resolution_kind: ReconciliationResolutionKind | str,
            verification_status: ReconciliationVerificationStatus | str,
            recorded_by_user_id: uuid.UUID,
            basis_refs: Iterable[str],
            evidence_refs: Iterable[str] = (),
        ) -> ReconciliationResolution:
            \"\"\"Append disposition/verification without rewriting diff lifecycle.\"\"\"
            diff = db.get(ReconciliationDiff, reconciliation_diff_id)
            if diff is None:
                raise NotFoundError("reconciliation diff not found")
            kind = ReconciliationResolutionKind(resolution_kind)
            verification = ReconciliationVerificationStatus(verification_status)
            bases = _refs(basis_refs)
            evidence = _refs(evidence_refs)
            if not bases:
                raise ValidationError("reconciliation resolution requires basis_refs")
            if verification is not ReconciliationVerificationStatus.UNVERIFIED and not evidence:
                raise ValidationError(
                    f"{verification.value} reconciliation verification requires evidence_refs"
                )
            resolution = ReconciliationResolution(
                reconciliation_diff_id=diff.id,
                resolution_kind=kind,
                verification_status=verification,
                basis_refs=bases,
                evidence_refs=evidence,
                recorded_by_user_id=_require_actor(db, recorded_by_user_id),
            )
            db.add(resolution)
            db.flush()
            return resolution


        def latest_reconciliation_resolution(
            db, reconciliation_diff_id: uuid.UUID
        ) -> ReconciliationResolution | None:
            return (
                db.execute(
                    select(ReconciliationResolution)
                    .where(
                        ReconciliationResolution.reconciliation_diff_id
                        == reconciliation_diff_id
                    )
                    .order_by(
                        ReconciliationResolution.recorded_at.desc(),
                        ReconciliationResolution.id.desc(),
                    )
                    .limit(1)
                )
                .scalars()
                .first()
            )


        __all__ = [
            "append_effect_realization_assessment",
            "append_reconciliation_resolution",
            "latest_effect_realization_assessment",
            "latest_reconciliation_resolution",
        ]
        """,
    )

    write(
        "app/api/v1/semantic_records.py",
        """
        \"\"\"Append-only M2 semantic judgment endpoints.\"\"\"

        from __future__ import annotations

        import uuid
        from typing import Annotated

        from fastapi import APIRouter, Depends
        from pydantic import BaseModel, Field
        from sqlalchemy.orm import Session

        from app.api.deps import get_current_user, get_session, require_roles
        from app.models.effect_realization import EffectRealizationStatus
        from app.models.reconciliation_resolution import (
            ReconciliationResolutionKind,
            ReconciliationVerificationStatus,
        )
        from app.services.realization_resolution import (
            append_effect_realization_assessment,
            append_reconciliation_resolution,
        )

        router = APIRouter(prefix="/v1", tags=["semantic-records"])


        class EffectRealizationCreate(BaseModel):
            effect_id: uuid.UUID
            realization_status: EffectRealizationStatus
            evidence_refs: list[str] = Field(default_factory=list)


        class ReconciliationResolutionCreate(BaseModel):
            reconciliation_diff_id: uuid.UUID
            resolution_kind: ReconciliationResolutionKind
            verification_status: ReconciliationVerificationStatus
            basis_refs: list[str] = Field(min_length=1)
            evidence_refs: list[str] = Field(default_factory=list)


        @router.post("/effect-realizations")
        def create_effect_realization(
            body: EffectRealizationCreate,
            db: Annotated[Session, Depends(get_session)],
            user_id: Annotated[uuid.UUID, Depends(get_current_user)],
            _authorized: Annotated[bool, Depends(require_roles("system_admin"))],
        ) -> dict[str, object]:
            assessment = append_effect_realization_assessment(
                db,
                effect_id=body.effect_id,
                realization_status=body.realization_status,
                assessed_by_user_id=user_id,
                evidence_refs=body.evidence_refs,
            )
            return {
                "assessmentId": str(assessment.id),
                "effectId": str(assessment.effect_id),
                "realizationStatus": assessment.realization_status.value,
                "assessedByUserId": str(assessment.assessed_by_user_id),
                "assessedAt": assessment.assessed_at.isoformat(),
            }


        @router.post("/reconciliation-resolutions")
        def create_reconciliation_resolution(
            body: ReconciliationResolutionCreate,
            db: Annotated[Session, Depends(get_session)],
            user_id: Annotated[uuid.UUID, Depends(get_current_user)],
            _authorized: Annotated[
                bool, Depends(require_roles("accountant", "system_admin"))
            ],
        ) -> dict[str, object]:
            resolution = append_reconciliation_resolution(
                db,
                reconciliation_diff_id=body.reconciliation_diff_id,
                resolution_kind=body.resolution_kind,
                verification_status=body.verification_status,
                recorded_by_user_id=user_id,
                basis_refs=body.basis_refs,
                evidence_refs=body.evidence_refs,
            )
            return {
                "resolutionId": str(resolution.id),
                "reconciliationDiffId": str(resolution.reconciliation_diff_id),
                "resolutionKind": resolution.resolution_kind.value,
                "verificationStatus": resolution.verification_status.value,
                "recordedByUserId": str(resolution.recorded_by_user_id),
                "recordedAt": resolution.recorded_at.isoformat(),
            }
        """,
    )

    write(
        "alembic/versions/0008_realization_resolution.py",
        """
        \"\"\"append-only effect realization and reconciliation resolution records

        Revision ID: 0008_realization_resolution
        Revises: 0007_publication_qualification
        Create Date: 2026-08-24

        Historical lifecycle/execution states are never converted into M2 records.
        \"\"\"

        from __future__ import annotations

        import sqlalchemy as sa

        from alembic import op

        revision = "0008_realization_resolution"
        down_revision = "0007_publication_qualification"
        branch_labels = None
        depends_on = None


        def upgrade() -> None:
            op.create_table(
                "effect_realization_assessment",
                sa.Column("effect_id", sa.Uuid(), nullable=False),
                sa.Column("realization_status", sa.String(length=16), nullable=False),
                sa.Column("evidence_refs", sa.JSON(), nullable=False),
                sa.Column("assessed_by_user_id", sa.Uuid(), nullable=False),
                sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.ForeignKeyConstraint(
                    ["assessed_by_user_id"],
                    ["user.id"],
                    name="fk_effect_realization_assessment_assessed_by_user_id_user",
                ),
                sa.ForeignKeyConstraint(
                    ["effect_id"],
                    ["effect_ledger_entry.id"],
                    name="fk_effect_realization_assessment_effect_id_effect_ledger_entry",
                ),
                sa.PrimaryKeyConstraint("id", name="pk_effect_realization_assessment"),
            )
            op.create_index(
                "ix_effect_realization_assessment_effect_id",
                "effect_realization_assessment",
                ["effect_id"],
                unique=False,
            )
            op.create_table(
                "reconciliation_resolution",
                sa.Column("reconciliation_diff_id", sa.Uuid(), nullable=False),
                sa.Column("resolution_kind", sa.String(length=32), nullable=False),
                sa.Column("verification_status", sa.String(length=16), nullable=False),
                sa.Column("basis_refs", sa.JSON(), nullable=False),
                sa.Column("evidence_refs", sa.JSON(), nullable=False),
                sa.Column("recorded_by_user_id", sa.Uuid(), nullable=False),
                sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.ForeignKeyConstraint(
                    ["recorded_by_user_id"],
                    ["user.id"],
                    name="fk_reconciliation_resolution_recorded_by_user_id_user",
                ),
                sa.ForeignKeyConstraint(
                    ["reconciliation_diff_id"],
                    ["reconciliation_diff.id"],
                    name="fk_reconciliation_resolution_reconciliation_diff_id_reconciliation_diff",
                ),
                sa.PrimaryKeyConstraint("id", name="pk_reconciliation_resolution"),
            )
            op.create_index(
                "ix_reconciliation_resolution_reconciliation_diff_id",
                "reconciliation_resolution",
                ["reconciliation_diff_id"],
                unique=False,
            )


        def downgrade() -> None:
            op.drop_index(
                "ix_reconciliation_resolution_reconciliation_diff_id",
                table_name="reconciliation_resolution",
            )
            op.drop_table("reconciliation_resolution")
            op.drop_index(
                "ix_effect_realization_assessment_effect_id",
                table_name="effect_realization_assessment",
            )
            op.drop_table("effect_realization_assessment")
        """,
    )

    write(
        "tests/test_m2_realization_resolution.py",
        """
        from __future__ import annotations

        import uuid

        import pytest

        from app.core.errors import ValidationError
        from app.models.effect import EffectLedgerEntry, EffectStatus
        from app.models.effect_realization import (
            EffectRealizationAssessment,
            EffectRealizationStatus,
        )
        from app.models.reconciliation import (
            ReconciliationDiff,
            ReconciliationDiffStatus,
            ReconciliationRun,
            ReconciliationRunStatus,
        )
        from app.models.reconciliation_resolution import (
            ReconciliationResolution,
            ReconciliationResolutionKind,
            ReconciliationVerificationStatus,
        )
        from app.services.realization_resolution import (
            append_effect_realization_assessment,
            append_reconciliation_resolution,
            latest_effect_realization_assessment,
            latest_reconciliation_resolution,
        )


        def _effect(db, status: EffectStatus) -> EffectLedgerEntry:
            effect = EffectLedgerEntry(
                intent_id=uuid.uuid4(),
                target_system="shopify",
                operation="product_publish",
                status=status,
            )
            db.add(effect)
            db.flush()
            return effect


        def _diff(db, status: ReconciliationDiffStatus) -> ReconciliationDiff:
            run = ReconciliationRun(
                run_type="m2-test", status=ReconciliationRunStatus.COMPLETED_WITH_DIFFS
            )
            db.add(run)
            db.flush()
            diff = ReconciliationDiff(
                run_id=run.id,
                domain="effect",
                entity_type="effect",
                entity_id=str(uuid.uuid4()),
                status=status,
            )
            db.add(diff)
            db.flush()
            return diff


        def test_succeeded_effect_can_remain_unknown_or_unverified(db, make_user) -> None:
            actor = make_user(["system_admin"])
            effect = _effect(db, EffectStatus.SUCCEEDED)
            unknown = append_effect_realization_assessment(
                db,
                effect_id=effect.id,
                realization_status=EffectRealizationStatus.UNKNOWN,
                assessed_by_user_id=actor,
                evidence_refs=["check:remote-timeout"],
            )
            unverified = append_effect_realization_assessment(
                db,
                effect_id=effect.id,
                realization_status=EffectRealizationStatus.UNVERIFIED,
                assessed_by_user_id=actor,
            )
            assert unknown.realization_status is EffectRealizationStatus.UNKNOWN
            assert unverified.realization_status is EffectRealizationStatus.UNVERIFIED
            assert effect.status is EffectStatus.SUCCEEDED


        def test_outcome_unknown_can_later_be_verified_append_only(db, make_user) -> None:
            actor = make_user(["system_admin"])
            effect = _effect(db, EffectStatus.OUTCOME_UNKNOWN)
            first = append_effect_realization_assessment(
                db,
                effect_id=effect.id,
                realization_status=EffectRealizationStatus.UNKNOWN,
                assessed_by_user_id=actor,
                evidence_refs=["observation:first"],
            )
            second = append_effect_realization_assessment(
                db,
                effect_id=effect.id,
                realization_status=EffectRealizationStatus.VERIFIED,
                assessed_by_user_id=actor,
                evidence_refs=["observation:readback"],
            )
            assert first.id != second.id
            assert first.realization_status is EffectRealizationStatus.UNKNOWN
            assert latest_effect_realization_assessment(db, effect.id).id == second.id
            assert effect.status is EffectStatus.OUTCOME_UNKNOWN


        def test_strong_realization_judgment_requires_evidence(db, make_user) -> None:
            actor = make_user(["system_admin"])
            effect = _effect(db, EffectStatus.SUCCEEDED)
            with pytest.raises(ValidationError, match="requires evidence_refs"):
                append_effect_realization_assessment(
                    db,
                    effect_id=effect.id,
                    realization_status=EffectRealizationStatus.VERIFIED,
                    assessed_by_user_id=actor,
                )


        def test_historical_resolved_diff_has_no_synthetic_resolution(db) -> None:
            diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
            assert latest_reconciliation_resolution(db, diff.id) is None


        def test_resolution_write_does_not_mutate_diff_lifecycle(db, make_user) -> None:
            actor = make_user(["accountant"])
            diff = _diff(db, ReconciliationDiffStatus.MANUAL_RECONCILIATION)
            resolution = append_reconciliation_resolution(
                db,
                reconciliation_diff_id=diff.id,
                resolution_kind=ReconciliationResolutionKind.REPAIRED,
                verification_status=ReconciliationVerificationStatus.UNVERIFIED,
                recorded_by_user_id=actor,
                basis_refs=["basis:operator-action"],
            )
            assert resolution.verification_status is ReconciliationVerificationStatus.UNVERIFIED
            assert diff.status is ReconciliationDiffStatus.MANUAL_RECONCILIATION


        def test_disposition_and_verification_evolve_by_append_not_update(db, make_user) -> None:
            actor = make_user(["accountant"])
            diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
            first = append_reconciliation_resolution(
                db,
                reconciliation_diff_id=diff.id,
                resolution_kind=ReconciliationResolutionKind.ACCEPTED_DIFFERENCE,
                verification_status=ReconciliationVerificationStatus.UNVERIFIED,
                recorded_by_user_id=actor,
                basis_refs=["basis:policy-exception"],
            )
            second = append_reconciliation_resolution(
                db,
                reconciliation_diff_id=diff.id,
                resolution_kind=ReconciliationResolutionKind.ACCEPTED_DIFFERENCE,
                verification_status=ReconciliationVerificationStatus.VERIFIED,
                recorded_by_user_id=actor,
                basis_refs=["basis:policy-exception"],
                evidence_refs=["verification:independent-check"],
            )
            assert first.id != second.id
            assert first.verification_status is ReconciliationVerificationStatus.UNVERIFIED
            assert latest_reconciliation_resolution(db, diff.id).id == second.id
            assert diff.status is ReconciliationDiffStatus.RESOLVED


        def test_api_verified_realization_retains_assessor_identity(
            client, db, make_user, auth_headers
        ) -> None:
            actor = make_user(["system_admin"])
            effect = _effect(db, EffectStatus.SUCCEEDED)
            response = client.post(
                "/v1/effect-realizations",
                json={
                    "effect_id": str(effect.id),
                    "realization_status": "verified",
                    "evidence_refs": ["readback:product"],
                },
                headers=auth_headers(actor, ["system_admin"]),
            )
            assert response.status_code == 200, response.text
            row = db.get(
                EffectRealizationAssessment, uuid.UUID(response.json()["assessmentId"])
            )
            assert row.assessed_by_user_id == actor
            assert response.json()["assessedByUserId"] == str(actor)


        def test_api_reconciliation_resolution_retains_recorder_identity(
            client, db, make_user, auth_headers
        ) -> None:
            actor = make_user(["accountant"])
            diff = _diff(db, ReconciliationDiffStatus.RESOLVED)
            response = client.post(
                "/v1/reconciliation-resolutions",
                json={
                    "reconciliation_diff_id": str(diff.id),
                    "resolution_kind": "accepted_difference",
                    "verification_status": "verified",
                    "basis_refs": ["basis:policy-exception"],
                    "evidence_refs": ["verification:review"],
                },
                headers=auth_headers(actor, ["accountant"]),
            )
            assert response.status_code == 200, response.text
            row = db.get(
                ReconciliationResolution, uuid.UUID(response.json()["resolutionId"])
            )
            assert row.recorded_by_user_id == actor
            assert response.json()["recordedByUserId"] == str(actor)
        """,
    )

    write(
        "tests/test_m2_migration_no_backfill.py",
        """
        from __future__ import annotations

        import types
        import uuid
        from pathlib import Path

        import sqlalchemy as sa
        from alembic import command
        from alembic.config import Config
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.ext.compiler import compiles
        from sqlalchemy.orm import Session

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


        def test_0008_does_not_backfill_historical_lifecycle_states(
            tmp_path, monkeypatch
        ) -> None:
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
                    conn.execute(
                        sa.text("SELECT count(*) FROM effect_realization_assessment")
                    ).scalar_one()
                    == 0
                )
                assert (
                    conn.execute(
                        sa.text("SELECT count(*) FROM reconciliation_resolution")
                    ).scalar_one()
                    == 0
                )
            engine.dispose()
        """,
    )

    models = "app/models/__init__.py"
    replace(
        models,
        "from app.models.effect import EffectLedgerEntry, EffectStatus\n",
        "from app.models.effect import EffectLedgerEntry, EffectStatus\n"
        "from app.models.effect_realization import (\n"
        "    EffectRealizationAssessment,\n"
        "    EffectRealizationStatus,\n"
        ")\n",
    )
    replace(
        models,
        "from app.models.returns import ReturnCase, ReturnDisposition, ReturnStatus\n",
        "from app.models.reconciliation_resolution import (\n"
        "    ReconciliationResolution,\n"
        "    ReconciliationResolutionKind,\n"
        "    ReconciliationVerificationStatus,\n"
        ")\n"
        "from app.models.returns import ReturnCase, ReturnDisposition, ReturnStatus\n",
    )
    replace(
        models,
        '    "EffectStatus",\n',
        '    "EffectStatus",\n'
        '    "EffectRealizationAssessment",\n'
        '    "EffectRealizationStatus",\n',
    )
    replace(
        models,
        '    "ReconciliationRunStatus",\n',
        '    "ReconciliationRunStatus",\n'
        '    "ReconciliationResolution",\n'
        '    "ReconciliationResolutionKind",\n'
        '    "ReconciliationVerificationStatus",\n',
    )

    api_init = "app/api/v1/__init__.py"
    replace(
        api_init,
        "    sales_orders,\n",
        "    sales_orders,\n    semantic_records,\n",
    )
    replace(
        api_init,
        '    "sales_orders",\n',
        '    "sales_orders",\n    "semantic_records",\n',
    )

    main = "app/main.py"
    replace(
        main,
        "    app.include_router(v1.sales_orders.router)\n",
        "    app.include_router(v1.sales_orders.router)\n"
        "    app.include_router(v1.semantic_records.router)\n",
    )


if __name__ == "__main__":
    main()
