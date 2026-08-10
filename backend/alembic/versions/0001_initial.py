"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-10

Hand-written initial migration: creates all 23 tables in dependency order with
the constraints and indexes declared by ``app.models``. Idempotent-safe: plain
``create_table`` calls, no data seeding.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
BYTEA = postgresql.BYTEA


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", UUID, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )

    op.create_table(
        "role_assignment",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignment"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_role_assignment_user_id_user"),
        sa.UniqueConstraint(
            "user_id", "role", "scope", name="uq_role_assignment_user_id_role_scope"
        ),
    )

    op.create_table(
        "workflow_run",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("input_json", JSONB, nullable=True),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_run"),
    )
    op.create_index("ix_workflow_run_status_updated_at", "workflow_run", ["status", "updated_at"])

    op.create_table(
        "work_item",
        sa.Column("id", UUID, nullable=False),
        sa.Column("workflow_id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("required_roles", JSONB, nullable=True),
        sa.Column("assignee_user_id", UUID, nullable=True),
        sa.Column("payload_json", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("decision_json", JSONB, nullable=True),
        sa.Column("decided_by_user_id", UUID, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_work_item"),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflow_run.id"], name="fk_work_item_workflow_id_workflow_run"
        ),
    )
    op.create_index("ix_work_item_status_expires_at", "work_item", ["status", "expires_at"])
    op.create_index("ix_work_item_workflow_id", "work_item", ["workflow_id"])

    op.create_table(
        "work_item_decision",
        sa.Column("id", UUID, nullable=False),
        sa.Column("work_item_id", UUID, nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("submitted_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_decision"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_item.id"], name="fk_work_item_decision_work_item_id_work_item"
        ),
    )

    op.create_table(
        "idempotency_record",
        sa.Column("id", UUID, nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_record"),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_record_scope_key"),
    )

    op.create_table(
        "outbox_event",
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("causation_id", sa.String(length=64), nullable=True),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_event"),
    )
    op.create_index(
        "ix_outbox_event_status_next_attempt_at",
        "outbox_event",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "inbox_event",
        sa.Column("id", UUID, nullable=False),
        sa.Column("consumer", sa.String(length=64), nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_event"),
        sa.UniqueConstraint("consumer", "event_id", name="uq_inbox_event_consumer_event_id"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", UUID, nullable=False),
        sa.Column("actor_user_id", UUID, nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("changes", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index(
        "ix_audit_log_resource_type_resource_id", "audit_log", ["resource_type", "resource_id"]
    )

    op.create_table(
        "feedback_cluster",
        sa.Column("id", UUID, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("items_json", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_cluster"),
    )

    op.create_table(
        "feedback_item",
        sa.Column("id", UUID, nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("customer_ref", sa.String(length=128), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("sanitized_text", sa.Text(), nullable=False),
        sa.Column("raw_payload_enc", BYTEA, nullable=True),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cluster_id", UUID, nullable=True),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_item"),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["feedback_cluster.id"],
            name="fk_feedback_item_cluster_id_feedback_cluster",
        ),
    )

    op.create_table(
        "catalog_change_candidate",
        sa.Column("id", UUID, nullable=False),
        sa.Column("source_refs", JSONB, nullable=True),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column("sanitizer_version", sa.String(length=16), nullable=True),
        sa.Column("model_id", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("rule_version", sa.String(length=16), nullable=True),
        sa.Column("proposal_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence", JSONB, nullable=True),
        sa.Column("proposal_json", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_user_id", UUID, nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_change_candidate"),
    )

    op.create_table(
        "catalog_revision",
        sa.Column("id", UUID, nullable=False),
        sa.Column("candidate_id", UUID, nullable=True),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current", JSONB, nullable=True),
        sa.Column("proposed", JSONB, nullable=True),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_revision"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["catalog_change_candidate.id"],
            name="fk_catalog_revision_candidate_id_catalog_change_candidate",
        ),
    )

    op.create_table(
        "listing_publication",
        sa.Column("id", UUID, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("shopify_product_gid", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("remote_reference", sa.String(length=255), nullable=True),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_listing_publication"),
    )

    op.create_table(
        "external_id_mapping",
        sa.Column("id", UUID, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("channel_metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_external_id_mapping"),
        sa.UniqueConstraint("sku", "channel", name="uq_external_id_mapping_sku_channel"),
    )

    op.create_table(
        "procurement_order",
        sa.Column("id", UUID, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("uom", sa.String(length=8), nullable=False),
        sa.Column("supplier", sa.String(length=128), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("odoo_po_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_procurement_order"),
    )

    op.create_table(
        "sales_order",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_ref", sa.String(length=64), nullable=False),
        sa.Column("shopify_order_id", sa.String(length=64), nullable=True),
        sa.Column("customer_ref", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("odoo_sale_order_id", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("items", JSONB, nullable=True),
        sa.Column("shipping", JSONB, nullable=True),
        sa.Column("fulfillment_status", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sales_order"),
        sa.UniqueConstraint("order_ref", name="uq_sales_order_order_ref"),
        sa.UniqueConstraint("shopify_order_id", name="uq_sales_order_shopify_order_id"),
    )

    op.create_table(
        "return_case",
        sa.Column("id", UUID, nullable=False),
        sa.Column("return_ref", sa.String(length=64), nullable=False),
        sa.Column("shopify_order_id", sa.String(length=64), nullable=True),
        sa.Column("order_ref", sa.String(length=64), nullable=True),
        sa.Column("customer_ref", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("odoo_return_move_id", sa.String(length=64), nullable=True),
        sa.Column("credit_note_id", sa.String(length=64), nullable=True),
        sa.Column("shopify_refund_gid", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_return_case"),
        sa.UniqueConstraint("return_ref", name="uq_return_case_return_ref"),
    )

    op.create_table(
        "reconciliation_run",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_run"),
    )

    op.create_table(
        "reconciliation_diff",
        sa.Column("id", UUID, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("expected", JSONB, nullable=True),
        sa.Column("actual", JSONB, nullable=True),
        sa.Column("difference", JSONB, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_diff"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["reconciliation_run.id"],
            name="fk_reconciliation_diff_run_id_reconciliation_run",
        ),
    )
    op.create_index("ix_reconciliation_diff_status", "reconciliation_diff", ["status"])

    op.create_table(
        "price_offer",
        sa.Column("id", UUID, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("proposed_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("margin_ok", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by", UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_price_offer"),
    )

    op.create_table(
        "effect_ledger_entry",
        sa.Column("id", UUID, nullable=False),
        sa.Column("intent_id", UUID, nullable=False),
        sa.Column("target_system", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("approval_ref", UUID, nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("remote_reference", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("compensation", sa.String(length=255), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_effect_ledger_entry"),
        sa.UniqueConstraint("intent_id", name="uq_effect_ledger_entry_intent_id"),
    )
    op.create_index(
        "ix_effect_ledger_entry_target_system_operation",
        "effect_ledger_entry",
        ["target_system", "operation"],
    )
    op.create_index("ix_effect_ledger_entry_status", "effect_ledger_entry", ["status"])


def downgrade() -> None:
    op.drop_table("effect_ledger_entry")
    op.drop_table("price_offer")
    op.drop_table("reconciliation_diff")
    op.drop_table("reconciliation_run")
    op.drop_table("return_case")
    op.drop_table("sales_order")
    op.drop_table("procurement_order")
    op.drop_table("external_id_mapping")
    op.drop_table("listing_publication")
    op.drop_table("catalog_revision")
    op.drop_table("catalog_change_candidate")
    op.drop_table("feedback_item")
    op.drop_table("feedback_cluster")
    op.drop_table("audit_log")
    op.drop_table("inbox_event")
    op.drop_table("outbox_event")
    op.drop_table("idempotency_record")
    op.drop_table("work_item_decision")
    op.drop_table("work_item")
    op.drop_table("workflow_run")
    op.drop_table("role_assignment")
    op.drop_table("user")
