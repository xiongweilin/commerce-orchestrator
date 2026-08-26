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
    op.add_column("responsibility_binding", sa.Column("workflow_ref", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_responsibility_binding_workflow_ref_workflow_run",
        "responsibility_binding",
        "workflow_run",
        ["workflow_ref"],
        ["id"],
    )
    op.create_index(
        "ix_responsibility_binding_workflow_ref",
        "responsibility_binding",
        ["workflow_ref"],
    )

    op.add_column("responsibility_obligation", sa.Column("workflow_ref", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_responsibility_obligation_workflow_ref_workflow_run",
        "responsibility_obligation",
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
    op.drop_constraint(
        "fk_responsibility_obligation_workflow_ref_workflow_run",
        "responsibility_obligation",
        type_="foreignkey",
    )
    op.drop_column("responsibility_obligation", "workflow_ref")

    op.drop_index("ix_responsibility_binding_workflow_ref", table_name="responsibility_binding")
    op.drop_constraint(
        "fk_responsibility_binding_workflow_ref_workflow_run",
        "responsibility_binding",
        type_="foreignkey",
    )
    op.drop_column("responsibility_binding", "workflow_ref")
