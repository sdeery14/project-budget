"""``projectbudget backup``: a consistent copy of the SQLite database (RFC v3 §7, ADR-001).

A plain file copy is unsafe under WAL because the write-ahead log is a second
file; ``VACUUM INTO`` writes a single, compacted, transactionally consistent
database file instead. PostgreSQL users take ``pg_dump``; both are also served
by ``projectbudget export``.
"""

import os
from pathlib import Path

from sqlalchemy.engine import make_url

from projectbudget.persistence.engine import backend_name, make_engine

PRIVATE_FILE_MODE = 0o600


class BackupError(Exception):
    """The backend has no file to copy, the source is missing, or the target cannot be written."""


def sqlite_file(url: str) -> Path:
    """The file behind a SQLite URL, or raise for other backends and in-memory databases."""
    if backend_name(url) != "sqlite":
        raise BackupError(
            "`projectbudget backup` copies a SQLite file; for PostgreSQL use pg_dump against the "
            "direct connection string, or `projectbudget export` for a JSON backup"
        )
    database = make_url(url).database
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        raise BackupError("An in-memory SQLite database has no file to back up")
    return Path(database)


def backup_sqlite(url: str, target: Path) -> Path:
    """Write a consistent copy of the SQLite database at ``url`` to ``target``.

    The source must exist (opening a missing path would create an empty database and
    "back up" nothing). The target is created exclusively with owner-only permissions
    before SQLite writes a byte into it, so the copy is never world-readable, not even
    briefly; SQLite accepts an empty target file for ``VACUUM INTO``.
    """
    source = sqlite_file(url)
    if not source.is_file():
        raise BackupError(f"No database at {source}; nothing to back up")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        raise BackupError(f"{target} already exists; backups are never overwritten") from exc
    os.close(handle)
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            # VACUUM INTO cannot run inside a transaction; the dialect starts none for this call.
            connection.exec_driver_sql("VACUUM INTO ?", (str(target),))
    except Exception:
        target.unlink(missing_ok=True)  # never leave a half-written backup behind
        raise
    finally:
        engine.dispose()
    return target
