"""The ``projectbudget`` CLI drives the same migration code as the container."""

import os

import pytest
from typer.testing import CliRunner

from projectbudget import __version__
from projectbudget.cli.main import app
from projectbudget.persistence import migrate

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == __version__


def test_db_upgrade_current_downgrade(database_url: str) -> None:
    head = migrate.head_revision()
    assert head is not None

    result = runner.invoke(app, ["--database-url", database_url, "db", "upgrade"])
    assert result.exit_code == 0, result.output
    assert f"revision {head}" in result.output

    result = runner.invoke(app, ["--database-url", database_url, "db", "current"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == head

    result = runner.invoke(app, ["--database-url", database_url, "db", "downgrade", "base"])
    assert result.exit_code == 0, result.output
    assert "revision base" in result.output

    result = runner.invoke(app, ["--database-url", database_url, "db", "current"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("base")
    assert "db upgrade" in result.output


def test_unsupported_backend_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROJECTBUDGET_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["--database-url", "mysql://u:p@h/db", "db", "current"])
    assert result.exit_code != 0
    assert "ADR-001" in result.output
    # The override must stay in memory; it is not written back to the environment.
    assert "PROJECTBUDGET_DATABASE_URL" not in os.environ


def test_option_beats_environment(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("PROJECTBUDGET_DATABASE_URL", "mysql://ignored")
    result = runner.invoke(app, ["--database-url", database_url, "db", "current"])
    assert result.exit_code == 0, result.output


def test_run_help_mentions_single_worker() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    assert "one worker" in result.output
