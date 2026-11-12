"""The ``projectbudget`` CLI drives the same migration and auth code as the app."""

import json
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete
from typer.testing import CliRunner

from conftest import OWNER_PASSWORD
from projectbudget import __version__
from projectbudget.cli.main import app
from projectbudget.fixtures.canonical import seed_canonical
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.models import Owner
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, catalog, projects, purchases, tokens, workspace

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


def test_passwd_resets_a_forgotten_password(migrated_url: str) -> None:
    base = ["--database-url", migrated_url]
    runner.invoke(
        app, [*base, "init", "--username", "sean", "--password-stdin"], input=OWNER_PASSWORD + "\n"
    )
    missing = runner.invoke(
        app,
        [*base, "passwd", "--username", "nobody", "--password-stdin"],
        input="whatever it was\n",
    )
    assert missing.exit_code == 1
    assert "No owner named 'nobody'" in missing.output
    weak = runner.invoke(
        app, [*base, "passwd", "--username", "sean", "--password-stdin"], input="short\n"
    )
    assert weak.exit_code == 2
    result = runner.invoke(
        app,
        [*base, "passwd", "--username", "Sean", "--password-stdin"],
        input="a brand new password\n",
    )
    assert result.exit_code == 0, result.output
    assert "Password set for 'sean'" in result.output
    assert "a brand new password" not in result.output
    engine = make_engine(migrated_url)
    try:
        factory = make_session_factory(engine)
        assert auth.authenticate(factory, "sean", OWNER_PASSWORD, limit_keys=["x"]) is None
        assert auth.authenticate(factory, "sean", "a brand new password", limit_keys=["x"])
    finally:
        engine.dispose()
    prompted = runner.invoke(
        app,
        [*base, "passwd", "--username", "sean"],
        input="another new password\nanother new password\n",
    )
    assert prompted.exit_code == 0, prompted.output
    assert "another new password" not in prompted.output


def test_reset_empties_the_workspace_but_keeps_the_owner_and_tokens(migrated_url: str) -> None:
    """#33: start over without deleting the database file or creating the owner again."""
    base = ["--database-url", migrated_url]
    runner.invoke(
        app, [*base, "init", "--username", "sean", "--password-stdin"], input=OWNER_PASSWORD + "\n"
    )
    engine = make_engine(migrated_url)
    try:
        factory = make_session_factory(engine)
        principal = auth.owner_principal(factory)
        seed_canonical(factory, principal, as_of=date(2026, 11, 1))
        issued = tokens.issue_token(factory, principal, "ci", read_only=True)

        declined = runner.invoke(app, [*base, "reset"], input="n\n")
        assert declined.exit_code != 0
        assert len(projects.list_projects(factory, principal)) == 2

        result = runner.invoke(app, [*base, "reset", "--yes"])
        assert result.exit_code == 0, result.output
        assert "Workspace emptied" in result.output
        assert projects.list_projects(factory, principal) == []
        assert catalog.list_items(factory, principal) == []
        assert purchases.list_purchases(factory, principal) == []
        assert auth.authenticate(factory, "sean", OWNER_PASSWORD, limit_keys=["x"]) is not None
        assert [t.id for t in tokens.list_tokens(factory, principal)] == [issued.info.id]
        assert workspace.get_currency(factory) == "USD"
    finally:
        engine.dispose()


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


def test_export_and_import_round_trip_through_the_cli(migrated_url: str, tmp_path: Path) -> None:
    base = ["--database-url", migrated_url]
    runner.invoke(
        app, [*base, "init", "--username", "sean", "--password-stdin"], input=OWNER_PASSWORD + "\n"
    )
    engine = make_engine(migrated_url)
    try:
        factory = make_session_factory(engine)
        seed_canonical(factory, auth.owner_principal(factory), as_of=date(2026, 11, 1))
    finally:
        engine.dispose()

    out = tmp_path / "export.json"
    result = runner.invoke(app, [*base, "export", "--output", str(out)])
    assert result.exit_code == 0, result.output
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["format"] == "projectbudget-workspace"
    assert len(document["catalog_items"]) == 4

    to_stdout = runner.invoke(app, [*base, "export"])
    assert to_stdout.exit_code == 0, to_stdout.output
    assert json.loads(to_stdout.output)["currency"] == "USD"

    refused = runner.invoke(app, [*base, "import", str(out)])
    assert refused.exit_code == 1
    assert "--replace" in refused.output

    replaced = runner.invoke(app, [*base, "import", str(out), "--replace"])
    assert replaced.exit_code == 0, replaced.output
    assert "Imported 2 projects, 4 catalog items" in replaced.output
    assert "previous data replaced" in replaced.output

    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    invalid = runner.invoke(app, [*base, "import", str(bad)])
    assert invalid.exit_code != 0
    assert "Not a ProjectBudget export" in invalid.output
    missing = runner.invoke(app, [*base, "import", str(tmp_path / "nope.json")])
    assert missing.exit_code != 0


def test_export_needs_an_owner(migrated_url: str) -> None:
    result = runner.invoke(app, ["--database-url", migrated_url, "export"])
    assert result.exit_code == 1
    assert "projectbudget init" in result.output


def test_a_bad_timezone_setting_stops_the_cli_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROJECTBUDGET_TIMEZONE", "Mars/Olympus_Mons")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 2, result.output
    assert "Unknown time zone 'Mars/Olympus_Mons'" in result.output
    assert "IANA" in result.output
