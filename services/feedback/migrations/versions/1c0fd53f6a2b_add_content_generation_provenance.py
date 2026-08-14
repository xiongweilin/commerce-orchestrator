"""add content generation provenance

Revision ID: 1c0fd53f6a2b
Revises: e13871bc9f27
Create Date: 2026-07-02 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1c0fd53f6a2b"
down_revision: str | Sequence[str] | None = "e13871bc9f27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("issue_clusters") as batch_op:
        batch_op.add_column(
            sa.Column(
                "narrative_source",
                sa.String(length=32),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch_op.add_column(
            sa.Column("narrative_workflow_version", sa.String(length=64), nullable=True)
        )
    with op.batch_alter_table("sop_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "generation_source",
                sa.String(length=32),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch_op.add_column(sa.Column("workflow_version", sa.String(length=64), nullable=True))
    with op.batch_alter_table("weekly_reports") as batch_op:
        batch_op.add_column(
            sa.Column(
                "generation_source",
                sa.String(length=32),
                nullable=False,
                server_default="deterministic",
            )
        )
        batch_op.add_column(sa.Column("workflow_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("weekly_reports") as batch_op:
        batch_op.drop_column("workflow_version")
        batch_op.drop_column("generation_source")
    with op.batch_alter_table("sop_candidates") as batch_op:
        batch_op.drop_column("workflow_version")
        batch_op.drop_column("generation_source")
    with op.batch_alter_table("issue_clusters") as batch_op:
        batch_op.drop_column("narrative_workflow_version")
        batch_op.drop_column("narrative_source")
