"""Programmatic Alembic: the only way migrations run in this project.

There is no ``alembic.ini``; configuration is built here so the CLI, the
container entrypoint and the tests all drive the same code path (ADR-010).
Revision ids are sequential four-digit numbers so the history reads in order.
"""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from projectbudget.persistence import models  # noqa: F401  (registers tables on Base)
from projectbudget.persistence.base import Base
from projectbudget.persistence.engine import make_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(url: str) -> Config:
    """Build an Alembic config pointing at the packaged migrations and the given URL."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # configparser interpolation: a literal % in a URL (e.g. an encoded password) must be doubled.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    """Apply migrations up to ``revision``."""
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    """Revert migrations down to ``revision`` (for example ``-1`` or ``base``)."""
    command.downgrade(alembic_config(url), revision)


def current_revision(url: str) -> str | None:
    """The revision the database is at, or None for an empty database."""
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def head_revision() -> str | None:
    """The newest revision in the packaged migration scripts."""
    return ScriptDirectory.from_config(alembic_config("sqlite://")).get_current_head()


def pending_model_changes(url: str) -> list[Any]:
    """Differences between the ORM models and the migrated schema.

    Empty means autogenerate would produce nothing: the migrations fully
    describe the models. CI fails if this is non-empty (RFC v3 §8).
    """
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "render_as_batch": True},
            )
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def create_revision(url: str, message: str, *, autogenerate: bool = True) -> None:
    """Write a new revision file with the next sequential id."""
    cfg = alembic_config(url)
    next_id = _next_revision_id(cfg)

    def _assign_sequential_id(
        _context: MigrationContext, _revision: Any, directives: list[MigrationScript]
    ) -> None:
        for script in directives:
            script.rev_id = next_id

    command.revision(
        cfg,
        message=message,
        autogenerate=autogenerate,
        process_revision_directives=_assign_sequential_id,
    )


def _next_revision_id(cfg: Config) -> str:
    script_dir = ScriptDirectory.from_config(cfg)
    existing = [int(rev.revision) for rev in script_dir.walk_revisions() if rev.revision.isdigit()]
    return f"{(max(existing) + 1 if existing else 1):04d}"
