"""Sync sources and runs, source references on projects, items and requirements (ADR-007).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("locator", sa.String(length=500), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("external_project_id", sa.String(length=100), nullable=True),
        sa.Column("token_env", sa.String(length=100), nullable=True),
        sa.Column("last_revision", sa.String(length=100), nullable=True),
        sa.Column("last_content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('file', 'csv', 'github')", name=op.f("ck_sync_sources_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_sources")),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.String(length=100), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('applied', 'noop', 'rejected', 'failed')", name=op.f("ck_sync_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sync_sources.id"],
            name=op.f("fk_sync_runs_source_id_sync_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_runs")),
    )
    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sync_runs_source_id"), ["source_id"], unique=False)

    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by_source_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_catalog_items_created_by_source_id"),
            ["created_by_source_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_catalog_items_created_by_source_id_sync_sources"),
            "sync_sources",
            ["created_by_source_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f("ix_projects_source_id"), ["source_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_projects_source_id_sync_sources"),
            "sync_sources",
            ["source_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f("ix_requirements_source_id"), ["source_id"], unique=False)
        batch_op.create_unique_constraint("source_external", ["source_id", "external_id"])
        batch_op.create_foreign_key(
            batch_op.f("fk_requirements_source_id_sync_sources"),
            "sync_sources",
            ["source_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("requirements", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_requirements_source_id_sync_sources"), type_="foreignkey"
        )
        batch_op.drop_constraint("source_external", type_="unique")
        batch_op.drop_index(batch_op.f("ix_requirements_source_id"))
        batch_op.drop_column("external_id")
        batch_op.drop_column("source_id")

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_projects_source_id_sync_sources"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_projects_source_id"))
        batch_op.drop_column("external_id")
        batch_op.drop_column("source_id")

    with op.batch_alter_table("catalog_items", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_catalog_items_created_by_source_id_sync_sources"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_catalog_items_created_by_source_id"))
        batch_op.drop_column("created_by_source_id")

    with op.batch_alter_table("sync_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sync_runs_source_id"))

    op.drop_table("sync_runs")
    op.drop_table("sync_sources")
