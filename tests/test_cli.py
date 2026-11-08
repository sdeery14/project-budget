"""The ``projectbudget`` CLI drives the same migration and auth code as the app."""

import os

import pytest
from sqlalchemy import delete
from typer.testing import CliRunner

from conftest import OWNER_PASSWORD
from projectbudget import __version__
from projectbudget.cli.main import app
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.models import Owner
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, workspace

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

    # Leave the shared database migrated for the tests that follow.
    result = runner.invoke(app, ["--database-url", database_url, "db", "upgrade"])
    assert result.exit_code == 0, result.output


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


def _owner_exists(url: str) -> bool:
    engine = make_engine(url)
    try:
        return auth.owner_exists(make_session_factory(engine))
    finally:
        engine.dispose()


def _currency(url: str) -> str:
    engine = make_engine(url)
    try:
        return workspace.get_currency(make_session_factory(engine))
    finally:
        engine.dispose()


def test_init_creates_the_owner_once(migrated_url: str) -> None:
    base = ["--database-url", migrated_url, "init", "--username", "Sean"]
    result = runner.invoke(app, [*base, "--password-stdin"], input=OWNER_PASSWORD + "\n")
    assert result.exit_code == 0, result.output
    assert "Owner 'sean' created" in result.output
    assert "currency: USD" in result.output
    assert "/login" in result.output
    assert _owner_exists(migrated_url)
    assert _currency(migrated_url) == "USD"

    again = runner.invoke(app, [*base, "--password-stdin"], input=OWNER_PASSWORD + "\n")
    assert again.exit_code == 1
    assert "already exists" in again.output


def test_init_prompts_and_confirms_the_password(migrated_url: str) -> None:
    result = runner.invoke(
        app,
        ["--database-url", migrated_url, "init", "--username", "sean"],
        input=f"{OWNER_PASSWORD}\n{OWNER_PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    assert OWNER_PASSWORD not in result.output
    assert _owner_exists(migrated_url)


def test_init_sets_the_workspace_currency(migrated_url: str) -> None:
    result = runner.invoke(
        app,
        [
            "--database-url",
            migrated_url,
            "init",
            "--username",
            "sean",
            "--password-stdin",
            "--currency",
            "eur",
        ],
        input=OWNER_PASSWORD + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "currency: EUR" in result.output
    assert _currency(migrated_url) == "EUR"


def test_init_rejects_an_invalid_currency_before_touching_the_database(
    migrated_url: str,
) -> None:
    result = runner.invoke(
        app,
        [
            "--database-url",
            migrated_url,
            "init",
            "--username",
            "sean",
            "--password-stdin",
            "--currency",
            "dollars",
        ],
        input=OWNER_PASSWORD + "\n",
    )
    assert result.exit_code != 0
    assert "ISO 4217" in result.output
    assert not _owner_exists(migrated_url)


def test_init_rejects_weak_passwords(migrated_url: str) -> None:
    result = runner.invoke(
        app,
        ["--database-url", migrated_url, "init", "--username", "sean", "--password-stdin"],
        input="short\n",
    )
    assert result.exit_code != 0
    assert "at least" in result.output
    assert not _owner_exists(migrated_url)


def test_init_applies_migrations_first(database_url: str) -> None:
    runner.invoke(app, ["--database-url", database_url, "db", "downgrade", "base"])
    result = runner.invoke(
        app,
        ["--database-url", database_url, "init", "--username", "sean", "--password-stdin"],
        input=OWNER_PASSWORD + "\n",
    )
    assert result.exit_code == 0, result.output
    assert migrate.current_revision(database_url) == migrate.head_revision()
    # Clean up the row this test created outside the migrated_url fixture.
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(delete(Owner))
    finally:
        engine.dispose()
