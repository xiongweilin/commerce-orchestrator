"""additive responsibility plane and explicit execution authority

Revision ID: 0009_responsibility_plane
Revises: 0008_realization_resolution
Create Date: 2026-08-26

No historical workflow/effect rows are backfilled as responsibility facts.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_responsibility_plane"
down_revision = "0008_realization_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "responsibility_record",
        sa.Column("id", sa.String(length=192), nullable=False),
        sa.Column("record_type", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_record"),
    )
    op.create_index("ix_responsibility_record_record_type", "responsibility_record", ["record_type"])

    op.create_table(
        "responsibility_knowledge_projection",
        sa.Column("id", sa.String(length=192), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_knowledge_projection"),
    )

    op.create_table(
        "responsibility_event",
        sa.Column("id", sa.String(length=192), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("subject_ref", sa.String(length=192), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_event"),
    )
    op.create_index("ix_responsibility_event_event_type", "responsibility_event", ["event_type"])
    op.create_index("ix_responsibility_event_subject_ref", "responsibility_event", ["subject_ref"])

    op.create_table(
        "responsibility_binding",
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_ref", sa.String(length=192), nullable=False),
        sa.Column("subject_version", sa.String(length=192), nullable=False),
        sa.Column("judgment_ref", sa.String(length=192), nullable=False),
        sa.Column("historical_use_ref", sa.String(length=192), nullable=False),
        sa.Column("requirement_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_binding"),
        sa.UniqueConstraint(
            "historical_use_ref",
            name="uq_responsibility_binding_historical_use_ref",
        ),
    )
    op.create_index("ix_responsibility_binding_subject_type", "responsibility_binding", ["subject_type"])
    op.create_index("ix_responsibility_binding_subject_ref", "responsibility_binding", ["subject_ref"])
    op.create_index("ix_responsibility_binding_judgment_ref", "responsibility_binding", ["judgment_ref"])

    op.create_table(
        "execution_authorization",
        sa.Column("authorization_key", sa.String(length=64), nullable=False),
        sa.Column("decision_ref", sa.Uuid(), nullable=False),
        sa.Column("workflow_ref", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_ref", sa.String(length=192), nullable=False),
        sa.Column("subject_version", sa.String(length=192), nullable=True),
        sa.Column("subject_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("target_system", sa.String(length=32), nullable=False),
        sa.Column("allowed_operations", sa.JSON(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("environment_ref", sa.String(length=192), nullable=False),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_ref"],
            ["work_item_decision.id"],
            name="fk_execution_authorization_decision_ref_work_item_decision",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_ref"],
            ["workflow_run.id"],
            name="fk_execution_authorization_workflow_ref_workflow_run",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"],
            ["user.id"],
            name="fk_execution_authorization_issued_by_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution_authorization"),
        sa.UniqueConstraint("authorization_key", name="uq_execution_authorization_authorization_key"),
    )
    op.create_index("ix_execution_authorization_decision_ref", "execution_authorization", ["decision_ref"])
    op.create_index("ix_execution_authorization_workflow_ref", "execution_authorization", ["workflow_ref"])
    op.create_index("ix_execution_authorization_subject_ref", "execution_authorization", ["subject_ref"])
    op.create_index(
        "ix_execution_authorization_issued_by_user_id",
        "execution_authorization",
        ["issued_by_user_id"],
    )

    op.create_table(
        "confirmed_outcome",
        sa.Column("effect_id", sa.Uuid(), nullable=False),
        sa.Column("realization_assessment_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_type", sa.String(length=96), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["effect_id"],
            ["effect_ledger_entry.id"],
            name="fk_confirmed_outcome_effect_id_effect_ledger_entry",
        ),
        sa.ForeignKeyConstraint(
            ["realization_assessment_id"],
            ["effect_realization_assessment.id"],
            name="fk_confirmed_outcome_realization_assessment_id",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["user.id"],
            name="fk_confirmed_outcome_confirmed_by_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_confirmed_outcome"),
        sa.UniqueConstraint(
            "realization_assessment_id",
            name="uq_confirmed_outcome_realization_assessment_id",
        ),
    )
    op.create_index(
        "ix_confirmed_outcome_effect_id",
        "confirmed_outcome",
        ["effect_id"],
        unique=True,
    )
    op.create_index(
        "ix_confirmed_outcome_confirmed_by_user_id",
        "confirmed_outcome",
        ["confirmed_by_user_id"],
    )

    op.create_table(
        "responsibility_obligation",
        sa.Column("subject_ref", sa.String(length=192), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=192), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=1024), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("projection_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discharged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["user.id"],
            name="fk_responsibility_obligation_recorded_by_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_responsibility_obligation"),
    )
    op.create_index(
        "ix_responsibility_obligation_subject_ref",
        "responsibility_obligation",
        ["subject_ref"],
    )
    op.create_index(
        "ix_responsibility_obligation_source_ref",
        "responsibility_obligation",
        ["source_ref"],
    )
    op.create_index(
        "ix_responsibility_obligation_recorded_by_user_id",
        "responsibility_obligation",
        ["recorded_by_user_id"],
    )

    # Batch operations preserve PostgreSQL behavior while making the migration
    # executable in SQLite-backed migration tests, where ALTER CONSTRAINT is not
    # supported directly.
    with op.batch_alter_table("effect_ledger_entry") as batch_op:
        batch_op.add_column(sa.Column("workflow_ref", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("authorization_ref", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_effect_ledger_entry_workflow_ref_workflow_run",
            "workflow_run",
            ["workflow_ref"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_effect_ledger_entry_authorization_ref",
            "execution_authorization",
            ["authorization_ref"],
            ["id"],
        )
    op.create_index("ix_effect_ledger_entry_workflow_ref", "effect_ledger_entry", ["workflow_ref"])
    op.create_index(
        "ix_effect_ledger_entry_authorization_ref",
        "effect_ledger_entry",
        ["authorization_ref"],
    )

    with op.batch_alter_table("publication_qualification_assessment") as batch_op:
        batch_op.add_column(sa.Column("assessed_by_user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_pub_qual_assessment_assessed_by_user",
            "user",
            ["assessed_by_user_id"],
            ["id"],
        )
    op.create_index(
        "ix_publication_qualification_assessment_assessed_by_user_id",
        "publication_qualification_assessment",
        ["assessed_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_qualification_assessment_assessed_by_user_id",
        table_name="publication_qualification_assessment",
    )
    with op.batch_alter_table("publication_qualification_assessment") as batch_op:
        batch_op.drop_constraint(
            "fk_pub_qual_assessment_assessed_by_user",
            type_="foreignkey",
        )
        batch_op.drop_column("assessed_by_user_id")

    op.drop_index("ix_effect_ledger_entry_authorization_ref", table_name="effect_ledger_entry")
    op.drop_index("ix_effect_ledger_entry_workflow_ref", table_name="effect_ledger_entry")
    with op.batch_alter_table("effect_ledger_entry") as batch_op:
        batch_op.drop_constraint(
            "fk_effect_ledger_entry_authorization_ref",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_effect_ledger_entry_workflow_ref_workflow_run",
            type_="foreignkey",
        )
        batch_op.drop_column("authorization_ref")
        batch_op.drop_column("workflow_ref")

    op.drop_index(
        "ix_responsibility_obligation_recorded_by_user_id",
        table_name="responsibility_obligation",
    )
    op.drop_index("ix_responsibility_obligation_source_ref", table_name="responsibility_obligation")
    op.drop_index("ix_responsibility_obligation_subject_ref", table_name="responsibility_obligation")
    op.drop_table("responsibility_obligation")
    op.drop_index("ix_confirmed_outcome_confirmed_by_user_id", table_name="confirmed_outcome")
    op.drop_index("ix_confirmed_outcome_effect_id", table_name="confirmed_outcome")
    op.drop_table("confirmed_outcome")
    op.drop_index(
        "ix_execution_authorization_issued_by_user_id",
        table_name="execution_authorization",
    )
    op.drop_index("ix_execution_authorization_subject_ref", table_name="execution_authorization")
    op.drop_index("ix_execution_authorization_workflow_ref", table_name="execution_authorization")
    op.drop_index("ix_execution_authorization_decision_ref", table_name="execution_authorization")
    op.drop_table("execution_authorization")
    op.drop_index("ix_responsibility_binding_judgment_ref", table_name="responsibility_binding")
    op.drop_index("ix_responsibility_binding_subject_ref", table_name="responsibility_binding")
    op.drop_index("ix_responsibility_binding_subject_type", table_name="responsibility_binding")
    op.drop_table("responsibility_binding")
    op.drop_index("ix_responsibility_event_subject_ref", table_name="responsibility_event")
    op.drop_index("ix_responsibility_event_event_type", table_name="responsibility_event")
    op.drop_table("responsibility_event")
    op.drop_table("responsibility_knowledge_projection")
    op.drop_index("ix_responsibility_record_record_type", table_name="responsibility_record")
    op.drop_table("responsibility_record")
