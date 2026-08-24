"""append-only publication qualification assessments

Revision ID: 0007_publication_qualification
Revises: 0006_remote_entity_ids
Create Date: 2026-08-24

Historical catalog revisions are not backfilled: OFFICIAL does not imply a
current publication qualification assessment.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_publication_qualification"
down_revision = "0006_remote_entity_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication_qualification_assessment",
        sa.Column("catalog_revision_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("source_revision_refs", sa.JSON(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("environment_ref", sa.String(length=128), nullable=False),
        sa.Column("assessment_status", sa.String(length=32), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_revision_id"],
            ["catalog_revision.id"],
            name="fk_publication_qualification_assessment_catalog_revision_id_catalog_revision",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publication_qualification_assessment"),
    )
    op.create_index(
        "ix_publication_qualification_assessment_catalog_revision_id",
        "publication_qualification_assessment",
        ["catalog_revision_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_qualification_assessment_catalog_revision_id",
        table_name="publication_qualification_assessment",
    )
    op.drop_table("publication_qualification_assessment")
