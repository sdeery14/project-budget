"""``projectbudget backup``: a consistent SQLite copy that opens and holds the data (RFC v3 §7)."""

import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import OWNER_PASSWORD
from projectbudget.cli.main import app
from projectbudget.domain.principal import Principal
from projectbudget.persistence import backup, migrate
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import projects

runner = CliRunner()


def test_backup_sqlite_writes_a_consistent_copy(
    session_factory: SessionFactory,
    principal: Principal,
    database_url: str,
    backend: str,
    tmp_path: Path,
) -> None:
    if backend != "sqlite":
        pytest.skip("VACUUM INTO is SQLite only; PostgreSQL uses pg_dump")
    projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    target = tmp_path / "backups" / "copy.sqlite"
    written = backup.backup_sqlite(database_url, target)
    assert written == target
    assert target.exists()
    with sqlite3.connect(target) as copy:
        assert copy.execute("SELECT name FROM projects").fetchall() == [("Shed",)]
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    # The copy is a full database: migrations report it at head.
    assert (
        migrate.current_revision(f"sqlite+pysqlite:///{target.as_posix()}")
        == migrate.head_revision()
    )
    with pytest.raises(backup.BackupError, match="never overwritten"):
        backup.backup_sqlite(database_url, target)


def test_backup_refuses_other_backends(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="pg_dump"):
        backup.backup_sqlite("postgresql+psycopg://u:p@h/db", tmp_path / "x.sqlite")


def test_backup_command(
    migrated_url: str, backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend != "sqlite":
        pytest.skip("VACUUM INTO is SQLite only; PostgreSQL uses pg_dump")
    monkeypatch.setenv("PROJECTBUDGET_DATA_DIR", str(tmp_path / "data"))
    base = ["--database-url", migrated_url]
    runner.invoke(
        app, [*base, "init", "--username", "sean", "--password-stdin"], input=OWNER_PASSWORD + "\n"
    )
    explicit = runner.invoke(
        app, [*base, "backup", "--output", str(tmp_path / "b" / "manual.sqlite")]
    )
    assert explicit.exit_code == 0, explicit.output
    assert (tmp_path / "b" / "manual.sqlite").exists()
    default = runner.invoke(app, [*base, "backup"])
    assert default.exit_code == 0, default.output
    written = list((tmp_path / "data" / "backups").glob("projectbudget-*.sqlite"))
    assert len(written) == 1
    assert "Backup written" in default.output
    again = runner.invoke(app, [*base, "backup", "--output", str(tmp_path / "b" / "manual.sqlite")])
    assert again.exit_code == 1
    assert "never overwritten" in again.output


def test_backup_refuses_a_missing_source_instead_of_creating_an_empty_one(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite"
    with pytest.raises(backup.BackupError, match="No database at"):
        backup.backup_sqlite(f"sqlite+pysqlite:///{missing.as_posix()}", tmp_path / "copy.sqlite")
    assert not missing.exists()  # opening it would have created an empty database
    assert not (tmp_path / "copy.sqlite").exists()


@pytest.mark.parametrize("url", ["sqlite+pysqlite:///:memory:", "sqlite://"])
def test_backup_refuses_in_memory_databases(url: str, tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="in-memory"):
        backup.backup_sqlite(url, tmp_path / "copy.sqlite")


def test_backup_target_is_created_private_before_sqlite_writes_it(
    session_factory: SessionFactory,
    principal: Principal,
    database_url: str,
    backend: str,
    tmp_path: Path,
) -> None:
    if backend != "sqlite":
        pytest.skip("VACUUM INTO is SQLite only; PostgreSQL uses pg_dump")
    target = tmp_path / "private.sqlite"
    seen_modes: list[int] = []
    real_open = os.open

    def recording_open(path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(os.fsdecode(path)) == target:
            seen_modes.append(mode)
            assert flags & os.O_EXCL, "the target must be created exclusively"
        return real_open(path, flags, mode)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(os, "open", recording_open)
        backup.backup_sqlite(database_url, target)
    assert seen_modes == [0o600]
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # A pre-existing target, even an empty one, is refused: backups never overwrite.
    with pytest.raises(backup.BackupError, match="never overwritten"):
        backup.backup_sqlite(database_url, target)
