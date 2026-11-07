"""Engine construction and the backend guarantees from ADR-001."""

import stat
import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from projectbudget.persistence.engine import (
    UnsupportedDatabaseError,
    backend_name,
    make_engine,
)
from projectbudget.settings import sqlite_url


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    """An orphan row must fail on both backends; SQLite only does this with the pragma on."""
    metadata = MetaData()
    parent = Table("fk_test_parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "fk_test_child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey(parent.c.id), nullable=False),
    )
    metadata.create_all(engine)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(child.insert().values(id=1, parent_id=999))
    finally:
        metadata.drop_all(engine)


def test_sqlite_connections_get_the_required_pragmas(engine: Engine, backend: str) -> None:
    if backend != "sqlite":
        pytest.skip("SQLite-only pragmas")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_sqlite_file_and_directory_are_private(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "pb.sqlite"
    engine = make_engine(sqlite_url(db_path))
    try:
        with engine.connect():
            pass
        # A second connection exercises the "already set" branch of the hook.
        with engine.connect():
            pass
    finally:
        engine.dispose()
    assert db_path.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(db_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_existing_directory_permissions_are_preserved(tmp_path: Path) -> None:
    """A relative URL like sqlite:///app.sqlite must not chmod the working directory."""
    existing = tmp_path / "shared"
    existing.mkdir()
    if sys.platform != "win32":
        existing.chmod(0o755)
    before = stat.S_IMODE(existing.stat().st_mode)
    engine = make_engine(sqlite_url(existing / "pb.sqlite"))
    try:
        with engine.connect():
            pass
    finally:
        engine.dispose()
    assert stat.S_IMODE(existing.stat().st_mode) == before


def test_in_memory_sqlite_needs_no_directory() -> None:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://user:pw@localhost/db",
        "mssql+pyodbc://user:pw@localhost/db",
        "oracle+oracledb://user:pw@localhost/db",
    ],
)
def test_other_backends_are_rejected(url: str) -> None:
    with pytest.raises(UnsupportedDatabaseError, match="ADR-001"):
        make_engine(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite+pysqlite:///x.sqlite", "sqlite"),
        ("sqlite:///x.sqlite", "sqlite"),
        ("postgresql+psycopg://u:p@h/db", "postgresql"),
        ("postgresql://u:p@h/db", "postgresql"),
    ],
)
def test_backend_name(url: str, expected: str) -> None:
    assert backend_name(url) == expected
