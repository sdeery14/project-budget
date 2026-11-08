"""``run --demo`` builds a throwaway, seeded SQLite workspace with a printed login (RFC v3 §3)."""

from pathlib import Path

from typer.testing import CliRunner

from projectbudget.cli import demo
from projectbudget.cli.main import app
from projectbudget.domain.principal import Principal
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, backlog, forecast

runner = CliRunner()


def test_prepare_demo_seeds_the_canonical_workspace(tmp_path: Path) -> None:
    workspace = demo.prepare_demo(tmp_path / "demo")
    assert workspace.directory == tmp_path / "demo"
    assert workspace.username == "demo"
    assert len(workspace.password) >= 16
    assert (tmp_path / "demo" / "demo.sqlite").exists()
    assert migrate.current_revision(workspace.database_url) == migrate.head_revision()

    engine = make_engine(workspace.database_url)
    try:
        factory = make_session_factory(engine)
        owner = auth.authenticate(
            factory, workspace.username, workspace.password, limit_keys=["demo-test"]
        )
        assert owner is not None
        principal = Principal(owner_id=owner.id, username=owner.username, auth="session")
        rows = backlog.get_backlog(factory, principal)
        assert {row.item.slug for row in rows.rows} == {
            "bar-clamp-600",
            "bench-vise-125",
            "m8-carriage-bolt-80",
        }
        assert rows.unpriced_count == 1
        schedule = forecast.get_forecast(factory, principal)
        assert schedule is not None
        assert not schedule.balance_stale
    finally:
        engine.dispose()


def test_prepare_demo_uses_a_fresh_temporary_directory_by_default() -> None:
    workspace = demo.prepare_demo(password="demo-password-123")
    try:
        assert workspace.directory.name.startswith("projectbudget-demo-")
        assert workspace.password == "demo-password-123"
        assert (workspace.directory / "demo.sqlite").exists()
    finally:
        for child in workspace.directory.glob("*"):
            child.unlink()
        workspace.directory.rmdir()


def test_run_help_documents_the_demo_flag() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.output
    assert "--demo" in result.output
    assert "fictional" in result.output.lower()
