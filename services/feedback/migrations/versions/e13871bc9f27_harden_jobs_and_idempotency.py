"""harden jobs and idempotency

Revision ID: e13871bc9f27
Revises: 979e40f9a3dc
Create Date: 2026-07-01 18:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e13871bc9f27"
down_revision: str | Sequence[str] | None = "979e40f9a3dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=128), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_session_idempotency", ["session_id", "idempotency_key"]
        )
    with op.batch_alter_table("analysis_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(
        "UPDATE analysis_jobs "
        "SET available_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )
    with op.batch_alter_table("analysis_jobs") as batch_op:
        batch_op.alter_column("available_at", nullable=False)
        batch_op.create_index(
            op.f("ix_analysis_jobs_available_at"), ["available_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs") as batch_op:
        batch_op.drop_index(op.f("ix_analysis_jobs_available_at"))
        batch_op.drop_column("available_at")
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_constraint("uq_session_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
