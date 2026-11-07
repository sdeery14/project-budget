"""Engine construction for the two supported backends (ADR-001).

SQLite needs three per-connection pragmas that the stdlib driver does not set:
``foreign_keys=ON`` (otherwise constraints are silently ignored), WAL journaling
(readers do not block the writer) and a busy timeout (the single writer waits
instead of failing). PostgreSQL needs nothing special. Any other backend is
rejected up front so a misconfiguration fails at startup, not at first query.
"""

import contextlib
import os
import stat
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry

SUPPORTED_BACKENDS = frozenset({"sqlite", "postgresql"})
SQLITE_BUSY_TIMEOUT_MS = 5_000


class UnsupportedDatabaseError(ValueError):
    """Raised for a URL whose backend is neither SQLite nor PostgreSQL."""


def backend_name(url: str) -> str:
    """Return ``sqlite`` or ``postgresql`` for a URL, or raise."""
    name = make_url(url).get_backend_name()
    if name not in SUPPORTED_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_BACKENDS))
        msg = f"Unsupported database backend {name!r}; ADR-001 allows only: {supported}"
        raise UnsupportedDatabaseError(msg)
    return name


def make_engine(url: str) -> Engine:
    """Create an engine with the backend-specific setup the RFC requires."""
    backend = backend_name(url)
    sa_url = make_url(url)
    engine = create_engine(sa_url)
    if backend == "sqlite":
        sqlite_path = _sqlite_file_path(sa_url.database)
        if sqlite_path is not None:
            _prepare_sqlite_directory(sqlite_path)
        event.listen(engine, "connect", _SqliteConnectHook(sqlite_path))
    return engine


def _sqlite_file_path(database: str | None) -> Path | None:
    """The on-disk path for a SQLite URL, or None for an in-memory database."""
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database)


def _prepare_sqlite_directory(path: Path) -> None:
    """Create the data directory, private to the current user where the OS allows."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    _chmod_quietly(directory, stat.S_IRWXU)


class _SqliteConnectHook:
    """Per-connection SQLite setup; also tightens the file mode on first connect."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._file_mode_set = False

    def __call__(self, dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()
        if self._path is not None and not self._file_mode_set and self._path.exists():
            _chmod_quietly(self._path, stat.S_IRUSR | stat.S_IWUSR)
            self._file_mode_set = True


def _chmod_quietly(path: Path, mode: int) -> None:
    """Best-effort chmod: Windows and some mounted volumes do not honour POSIX modes."""
    with contextlib.suppress(OSError):
        os.chmod(path, mode)
