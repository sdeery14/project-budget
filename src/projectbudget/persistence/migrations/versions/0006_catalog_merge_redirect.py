"""Catalog merge: the merged_into redirect on catalog items (RFC v3 §4, Milestone 3).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merged_into_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_catalog_items_merged_into_id"), ["merged_into_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_catalog_items_merged_into_id_catalog_items"),
            "catalog_items",
            ["merged_into_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_catalog_items_merged_into_id_catalog_items"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_catalog_items_merged_into_id"))
        batch_op.drop_column("merged_into_id")
