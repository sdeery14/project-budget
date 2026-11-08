"""``projectbudget backup``: a consistent copy of the SQLite database (RFC v3 §7, ADR-001).

A plain file copy is unsafe under WAL because the write-ahead log is a second
file; ``VACUUM INTO`` writes a single, compacted, transactionally consistent
database file instead. PostgreSQL users take ``pg_dump``; both are also served
by ``projectbudget export``.
"""

import os
import stat
import sys
from pathlib import Path

from projectbudget.persistence.engine import backend_name, make_engine


class BackupError(Exception):
    """The backend has no file to copy, or the target cannot be written."""


def backup_sqlite(url: str, target: Path) -> Path:
    """Write a consistent copy of the SQLite database at ``url`` to ``target`` (which must not exist)."""
    if backend_name(url) != "sqlite":
        raise BackupError(
            "`projectbudget backup` copies a SQLite file; for PostgreSQL use pg_dump against the "
            "direct connection string, or `projectbudget export` for a JSON backup"
        )
    if target.exists():
        raise BackupError(f"{target} already exists; backups are never overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            # VACUUM INTO cannot run inside a transaction; the dialect starts none for this call.
            connection.exec_driver_sql("VACUUM INTO ?", (str(target),))
    finally:
        engine.dispose()
    if sys.platform != "win32":
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return target
