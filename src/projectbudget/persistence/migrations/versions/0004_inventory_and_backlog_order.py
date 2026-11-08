"""Inventory movements and the workspace backlog order (RFC v3 §4, ADR-004).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backlog_order",
        sa.Column("catalog_item_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
            name=op.f("fk_backlog_order_catalog_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("catalog_item_id", name=op.f("pk_backlog_order")),
    )
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=False),
        sa.Column("delta_qty", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("requirement_id", sa.String(length=36), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "reason IN ('received', 'consumed', 'adjustment')",
            name=op.f("ck_inventory_movements_reason"),
        ),
        sa.CheckConstraint("delta_qty <> 0", name=op.f("ck_inventory_movements_delta_nonzero")),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
            name=op.f("fk_inventory_movements_catalog_item_id_catalog_items"),
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_inventory_movements_requirement_id_requirements"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_movements")),
    )
    with op.batch_alter_table("inventory_movements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_inventory_movements_catalog_item_id"), ["catalog_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_inventory_movements_requirement_id"), ["requirement_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_movements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_inventory_movements_requirement_id"))
        batch_op.drop_index(batch_op.f("ix_inventory_movements_catalog_item_id"))

    op.drop_table("inventory_movements")
    op.drop_table("backlog_order")
