"""add projection table

Revision ID: 0002_projection_table
Revises: 0001_initial
Create Date: 2026-08-10

The hand-written 0001 migration omitted ``projection`` although
``app.models.projections.Projection`` and the Shopify webhook ingest path
write to it. Adds the missing table (model-declared columns/constraints).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_projection_table"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    op.create_table(
        "projection",
        sa.Column("id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_projection"),
        sa.UniqueConstraint(
            "owner", "source", "external_id", name="uq_projection_owner_source_external_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("projection")
