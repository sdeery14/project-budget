"""Alembic environment. Driven only through ``projectbudget.persistence.migrate``."""

from alembic import context

from projectbudget.persistence import models  # noqa: F401  (registers tables on Base)
from projectbudget.persistence.base import Base
from projectbudget.persistence.engine import make_engine

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        msg = "sqlalchemy.url is not set; use projectbudget.persistence.migrate.alembic_config()"
        raise RuntimeError(msg)
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (``--sql`` mode)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    engine = make_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # SQLite cannot ALTER most things in place (ADR-001)
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
