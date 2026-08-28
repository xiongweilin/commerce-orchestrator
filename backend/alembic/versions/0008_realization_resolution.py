"""append-only effect realization and reconciliation resolution records

Revision ID: 0008_realization_resolution
Revises: 0007_publication_qualification
Create Date: 2026-08-24

Historical lifecycle/execution states are never converted into M2 records.
"""

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
            name="fk_reconciliation_resolution_diff_id",
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
