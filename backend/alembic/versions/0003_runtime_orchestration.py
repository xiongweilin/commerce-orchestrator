"""runtime orchestration columns, inbox lease/retry fields, runtime_heartbeat

Revision ID: 0003_runtime_orchestration
Revises: 0002_projection_table
Create Date: 2026-08-11

First of three in-place migrations for P7 shadow-run readiness (plan section
III.1 group 1).  All additions are additive (nullable or server-defaulted) so
older application code can keep running against the new schema:

- ``workflow_run``: ``orchestration_engine`` (default ``legacy_inline``),
  ``dbos_workflow_id``, ``initiated_by_user_id``, ``started_at``, ``finished_at``.
- ``work_item``: ``proposed_by_user_id``.
- ``inbox_event``: ``attempts``, ``next_attempt_at``, ``lease_until``,
  ``last_error``, ``processed_at`` plus the claim index
  ``(consumer, status, next_attempt_at)``.
- new ``runtime_heartbeat`` table (one row per process instance).

Existing workflow rows are explicitly backfilled to
``orchestration_engine = 'legacy_inline'``; new commands set ``dbos`` and
``workflow_version = 2`` (enforced by the workflow module, not this migration).

The new ``needs_reconciliation`` workflow status is a model-level enum value
only: status columns are VARCHAR (no CHECK), so no DDL is required for it.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_runtime_orchestration"
down_revision = "0002_projection_table"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB


def upgrade() -> None:
    # WorkflowRun: orchestration metadata (all additive).
    op.add_column(
        "workflow_run",
        sa.Column(
            "orchestration_engine",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_inline",
        ),
    )
    op.add_column(
        "workflow_run",
        sa.Column("dbos_workflow_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "workflow_run",
        sa.Column("initiated_by_user_id", UUID, nullable=True),
    )
    op.add_column(
        "workflow_run",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workflow_run",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Explicit backfill so every pre-existing row is legacy_inline, matching
    # the plan's compatibility rule for in-flight v1 workflows.
    op.execute(
        "UPDATE workflow_run SET orchestration_engine = 'legacy_inline' "
        "WHERE orchestration_engine IS NULL"
    )

    # WorkItem: explicit proposer, no longer inferred from payload JSON.
    op.add_column(
        "work_item",
        sa.Column("proposed_by_user_id", UUID, nullable=True),
    )

    # InboxEvent: lease/retry fields (additive) + claim index.
    op.add_column(
        "inbox_event",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "inbox_event",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbox_event",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbox_event",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "inbox_event",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_inbox_event_consumer_status_next_attempt_at",
        "inbox_event",
        ["consumer", "status", "next_attempt_at"],
    )

    # RuntimeHeartbeat: worker/API process liveness, one row per instance.
    op.create_table(
        "runtime_heartbeat",
        sa.Column("id", UUID, nullable=False),
        sa.Column("process_name", sa.String(length=64), nullable=False),
        sa.Column("instance_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_json", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_heartbeat"),
        sa.UniqueConstraint(
            "process_name",
            "instance_id",
            name="uq_runtime_heartbeat_process_name_instance_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("runtime_heartbeat")
    op.drop_index(
        "ix_inbox_event_consumer_status_next_attempt_at", table_name="inbox_event"
    )
    for column_name in (
        "processed_at",
        "last_error",
        "lease_until",
        "next_attempt_at",
        "attempts",
    ):
        op.drop_column("inbox_event", column_name)
    op.drop_column("work_item", "proposed_by_user_id")
    for column_name in (
        "finished_at",
        "started_at",
        "initiated_by_user_id",
        "dbos_workflow_id",
        "orchestration_engine",
    ):
        op.drop_column("workflow_run", column_name)
