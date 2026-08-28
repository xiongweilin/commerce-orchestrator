"""scope responsibility bindings and obligations to workflows

Revision ID: 0010_responsibility_workflow_refs
Revises: 0009_responsibility_plane
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_responsibility_workflow_refs"
down_revision = "0009_responsibility_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic revision identities are durable. This revision is 33 characters
    # long, so keep the version table permanently wide enough for it and for
    # future identifiers; downgrade intentionally does not shrink the column.
    with op.batch_alter_table("alembic_version") as batch_op:
        batch_op.alter_column(
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    with op.batch_alter_table("responsibility_binding") as batch_op:
        batch_op.add_column(sa.Column("workflow_ref", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_responsibility_binding_workflow_ref_workflow_run",
            "workflow_run",
            ["workflow_ref"],
            ["id"],
        )
    op.create_index(
        "ix_responsibility_binding_workflow_ref",
        "responsibility_binding",
        ["workflow_ref"],
    )

    with op.batch_alter_table("responsibility_obligation") as batch_op:
        batch_op.add_column(sa.Column("workflow_ref", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_responsibility_obligation_workflow_ref_workflow_run",
            "workflow_run",
            ["workflow_ref"],
            ["id"],
        )
    op.create_index(
        "ix_responsibility_obligation_workflow_ref",
        "responsibility_obligation",
        ["workflow_ref"],
    )


def downgrade() -> None:
    op.drop_index("ix_responsibility_obligation_workflow_ref", table_name="responsibility_obligation")
    with op.batch_alter_table("responsibility_obligation") as batch_op:
        batch_op.drop_constraint(
            "fk_responsibility_obligation_workflow_ref_workflow_run",
            type_="foreignkey",
        )
        batch_op.drop_column("workflow_ref")

    op.drop_index("ix_responsibility_binding_workflow_ref", table_name="responsibility_binding")
    with op.batch_alter_table("responsibility_binding") as batch_op:
        batch_op.drop_constraint(
            "fk_responsibility_binding_workflow_ref_workflow_run",
            type_="foreignkey",
        )
        batch_op.drop_column("workflow_ref")
