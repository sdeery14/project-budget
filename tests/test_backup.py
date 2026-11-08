"""``projectbudget backup``: a consistent SQLite copy that opens and holds the data (RFC v3 §7)."""

import sqlite3
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
