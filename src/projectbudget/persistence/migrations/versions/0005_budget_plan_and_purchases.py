"""Budget plan with month overrides, purchases with lines, and the receipt link on movements (ADR-006).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("earmarked_funds", sa.BigInteger(), nullable=False),
        sa.Column("balance_as_of", sa.Date(), nullable=False),
        sa.Column("protected_reserve", sa.BigInteger(), nullable=False),
        sa.Column("monthly_contribution", sa.BigInteger(), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=False),
        sa.Column("contingency_pct", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "contingency_pct BETWEEN 0 AND 100", name=op.f("ck_budget_plans_contingency_range")
        ),
        sa.CheckConstraint(
            "horizon_months BETWEEN 1 AND 120", name=op.f("ck_budget_plans_horizon_range")
        ),
        sa.CheckConstraint(
            "protected_reserve >= 0", name=op.f("ck_budget_plans_reserve_not_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_plans")),
    )
    op.create_table(
        "purchases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("vendor", sa.String(length=200), nullable=False),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("shipping", sa.BigInteger(), nullable=False),
        sa.Column("tax", sa.BigInteger(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned', 'ordered', 'received', 'cancelled')",
            name=op.f("ck_purchases_status"),
        ),
        sa.CheckConstraint("shipping >= 0", name=op.f("ck_purchases_shipping_not_negative")),
        sa.CheckConstraint("tax >= 0", name=op.f("ck_purchases_tax_not_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchases")),
    )
    op.create_table(
        "month_overrides",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["budget_plans.id"],
            name=op.f("fk_month_overrides_plan_id_budget_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_month_overrides")),
    )
    with op.batch_alter_table("month_overrides", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_month_overrides_plan_id"), ["plan_id"], unique=False)

    op.create_table(
        "purchase_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purchase_id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("received_qty", sa.Integer(), nullable=False),
        sa.CheckConstraint("qty > 0", name=op.f("ck_purchase_lines_qty_positive")),
        sa.CheckConstraint(
            "received_qty BETWEEN 0 AND qty", name=op.f("ck_purchase_lines_received_within_qty")
        ),
        sa.CheckConstraint(
            "unit_price >= 0", name=op.f("ck_purchase_lines_unit_price_not_negative")
        ),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
            name=op.f("fk_purchase_lines_catalog_item_id_catalog_items"),
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"],
            ["purchases.id"],
            name=op.f("fk_purchase_lines_purchase_id_purchases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_lines")),
    )
    with op.batch_alter_table("purchase_lines", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_purchase_lines_catalog_item_id"), ["catalog_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_purchase_lines_purchase_id"), ["purchase_id"], unique=False
        )

    with op.batch_alter_table("inventory_movements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("purchase_line_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_inventory_movements_purchase_line_id"),
            ["purchase_line_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_inventory_movements_purchase_line_id_purchase_lines"),
            "purchase_lines",
            ["purchase_line_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory_movements", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_inventory_movements_purchase_line_id_purchase_lines"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_inventory_movements_purchase_line_id"))
        batch_op.drop_column("purchase_line_id")

    with op.batch_alter_table("purchase_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_purchase_lines_purchase_id"))
        batch_op.drop_index(batch_op.f("ix_purchase_lines_catalog_item_id"))

    op.drop_table("purchase_lines")
    with op.batch_alter_table("month_overrides", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_month_overrides_plan_id"))

    op.drop_table("month_overrides")
    op.drop_table("purchases")
    op.drop_table("budget_plans")
