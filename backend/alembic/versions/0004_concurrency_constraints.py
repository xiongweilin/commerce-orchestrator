"""unique work item decision, CAS/claim indexes, idempotency record fix

Revision ID: 0004_concurrency_constraints
Revises: 0003_runtime_orchestration
Create Date: 2026-08-11

Second of three in-place migrations for P7 shadow-run readiness (plan section
III.1 group 2):

- Pre-check duplicate ``WorkItemDecision.work_item_id`` values; fail loudly
  (count + ids only, never payload/PII) instead of auto-deleting history.
- Add the unique constraint ``uq_work_item_decision_work_item_id`` so only one
  final decision can exist per work item (concurrent approvals: one wins).
- Indexes for optimistic-lock CAS on ``workflow_run(id, version)`` and
  ``work_item(id, version)``, plus the generic inbox claim scan
  ``(status, next_attempt_at)`` (the per-consumer claim index lives in 0003).
- ``idempotency_record.updated_at`` (server-defaulted, additive) and status
  vocabulary normalized to ``processing | completed``: legacy ``pending`` ->
  ``processing`` and legacy ``done`` -> ``completed``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_concurrency_constraints"
down_revision = "0003_runtime_orchestration"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def _duplicate_decision_work_item_ids(conn) -> list[str]:
    """Return ids of work items that already have more than one decision.

    Emits only the count and the ids (no payload / PII) per the plan.
    """
    rows = conn.execute(
        sa.text(
            "SELECT work_item_id, COUNT(*) AS decision_count "
            "FROM work_item_decision "
            "GROUP BY work_item_id "
            "HAVING COUNT(*) > 1 "
            "ORDER BY work_item_id"
        )
    ).fetchall()
    return [str(row[0]) for row in rows]


def upgrade() -> None:
    conn = op.get_bind()

    duplicate_ids = _duplicate_decision_work_item_ids(conn)
    if duplicate_ids:
        raise RuntimeError(
            "refusing to add uq_work_item_decision_work_item_id: "
            f"{len(duplicate_ids)} work item(s) already have multiple decisions; "
            "resolve them manually before migrating "
            f"(work_item_id values: {', '.join(duplicate_ids)})"
        )

    # SQLite cannot ALTER in place; batch mode recreates the table on SQLite
    # and executes a plain ADD CONSTRAINT on PostgreSQL.
    with op.batch_alter_table("work_item_decision", recreate="auto") as batch_op:
        batch_op.create_unique_constraint(
            "uq_work_item_decision_work_item_id", ["work_item_id"]
        )

    # Optimistic-lock CAS lookups: WHERE id = ? AND version = ?.
    op.create_index(
        "ix_workflow_run_id_version", "workflow_run", ["id", "version"]
    )
    op.create_index(
        "ix_work_item_id_version", "work_item", ["id", "version"]
    )
    # Generic inbox claim/backoff scan (per-consumer claim index is in 0003).
    op.create_index(
        "ix_inbox_event_status_next_attempt_at",
        "inbox_event",
        ["status", "next_attempt_at"],
    )

    # Idempotency record: additive updated_at + status vocabulary fix.
    # Batch mode so SQLite (which rejects ADD COLUMN with a non-constant
    # default) recreates the table; PostgreSQL applies a plain ADD COLUMN.
    with op.batch_alter_table("idempotency_record", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
    conn.execute(
        sa.text("UPDATE idempotency_record SET status = 'completed' WHERE status = 'done'")
    )
    conn.execute(
        sa.text(
            "UPDATE idempotency_record SET status = 'processing' WHERE status = 'pending'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_event_status_next_attempt_at", table_name="inbox_event")
    op.drop_index("ix_work_item_id_version", table_name="work_item")
    op.drop_index("ix_workflow_run_id_version", table_name="workflow_run")
    with op.batch_alter_table("work_item_decision", recreate="auto") as batch_op:
        batch_op.drop_constraint(
            "uq_work_item_decision_work_item_id", type_="unique"
        )
    with op.batch_alter_table("idempotency_record", recreate="auto") as batch_op:
        batch_op.drop_column("updated_at")
