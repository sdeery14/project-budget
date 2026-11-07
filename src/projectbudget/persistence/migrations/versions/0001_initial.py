"""Initial empty migration.

Establishes the Alembic version table and the sequential revision scheme.
The first tables arrive with the domain core (PR4a, ADR-003, ADR-004).

Revision ID: 0001
Revises:
Create Date: 2026-11-07
"""

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
