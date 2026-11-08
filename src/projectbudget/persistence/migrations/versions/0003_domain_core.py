"""Workspace settings, projects, catalog items, requirements and price estimates (RFC v3 §4).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("review_state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('consumable', 'reusable')", name=op.f("ck_catalog_items_kind")
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed', 'confirmed')",
            name=op.f("ck_catalog_items_review_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_items")),
        sa.UniqueConstraint("slug", name=op.f("uq_catalog_items_slug")),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_table(
        "workspace_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_settings")),
    )
    op.create_table(
        "price_estimates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')", name=op.f("ck_price_estimates_confidence")
        ),
        sa.CheckConstraint(
            "origin IN ('manual', 'manifest', 'purchase')", name=op.f("ck_price_estimates_origin")
        ),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_price_estimates_amount_not_negative")),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
            name=op.f("fk_price_estimates_catalog_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_estimates")),
    )
    with op.batch_alter_table("price_estimates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_price_estimates_catalog_item_id"), ["catalog_item_id"], unique=False
        )

    op.create_table(
        "requirements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("intent", sa.String(length=20), nullable=False),
        sa.Column("archive_reason", sa.String(length=255), nullable=True),
        sa.Column("sharing", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "intent IN ('active', 'paused', 'cancelled', 'archived')",
            name=op.f("ck_requirements_intent"),
        ),
        sa.CheckConstraint(
            "sharing IN ('shared', 'exclusive')", name=op.f("ck_requirements_sharing")
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 3", name=op.f("ck_requirements_priority_range")),
        sa.CheckConstraint("qty > 0", name=op.f("ck_requirements_qty_positive")),
        sa.ForeignKeyConstraint(
            ["catalog_item_id"],
            ["catalog_items.id"],
            name=op.f("fk_requirements_catalog_item_id_catalog_items"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_requirements_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirements")),
    )
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_requirements_catalog_item_id"), ["catalog_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_requirements_project_id"), ["project_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_requirements_project_id"))
        batch_op.drop_index(batch_op.f("ix_requirements_catalog_item_id"))

    op.drop_table("requirements")
    with op.batch_alter_table("price_estimates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_price_estimates_catalog_item_id"))

    op.drop_table("price_estimates")
    op.drop_table("workspace_settings")
    op.drop_table("projects")
    op.drop_table("catalog_items")
