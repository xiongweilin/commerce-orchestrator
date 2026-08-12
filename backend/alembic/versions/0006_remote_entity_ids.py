"""remote entity id columns for the O2C / procurement / return effect chains

Revision ID: 0006_remote_entity_ids
Revises: 0005_sensitive_payload
Create Date: 2026-08-12

P7 整改第 3 点：为远端实体 ID 建立正式领域列，validate effect 显式引用前序
create effect 的 remote_reference（odoo.picking_create -> stock.picking.id、
odoo.invoice_create -> account.move.id、odoo.bill_create -> account.move.id、
odoo.credit_note_create -> account.move.id），并由 finalize_after_effect 回写。
全部为可空列；存量行无需回填。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_remote_entity_ids"
down_revision = "0005_sensitive_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_order",
        sa.Column("odoo_picking_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sales_order",
        sa.Column("odoo_invoice_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "procurement_order",
        sa.Column("odoo_bill_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "return_case",
        sa.Column("odoo_credit_note_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("return_case", "odoo_credit_note_id")
    op.drop_column("procurement_order", "odoo_bill_id")
    op.drop_column("sales_order", "odoo_invoice_id")
    op.drop_column("sales_order", "odoo_picking_id")
