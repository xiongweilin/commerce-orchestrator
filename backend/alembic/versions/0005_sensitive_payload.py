"""sensitive payload vault table

Revision ID: 0005_sensitive_payload
Revises: 0004_concurrency_constraints
Create Date: 2026-08-11

Third of three in-place migrations for P7 shadow-run readiness (plan section
III.1 group 3).  Creates the ``sensitive_payload`` vault table; the encrypted
backfill of legacy plaintext columns is a worker background task (WP4 privacy
service) and is intentionally NOT part of this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_sensitive_payload"
down_revision = "0004_concurrency_constraints"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "sensitive_payload",
        sa.Column("id", UUID, nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sensitive_payload"),
    )
    # Retention cleanup scans by expiry; tombstone rows keep deleted_at set.
    op.create_index(
        "ix_sensitive_payload_expires_at", "sensitive_payload", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_sensitive_payload_expires_at", table_name="sensitive_payload")
    op.drop_table("sensitive_payload")
