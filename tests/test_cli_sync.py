"""``projectbudget sync``: dry run, apply, no-op, rejected manifests, unknown sources."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import OWNER_PASSWORD
from projectbudget.cli.main import app
from projectbudget.domain.catalog import SourceKind
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, sync

runner = CliRunner()

MANIFEST = """version: 1
project: {id: shed, name: Garden shed}
items:
  - {id: screws, name: Deck screws, qty: 100}
  - {id: saw, name: Hand saw, qty: 1, kind: reusable}
"""


@pytest.fixture
def env(
    migrated_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], str, Path]:
    monkeypatch.setenv("PROJECTBUDGET_SYNC_IMPORT_DIR", str(tmp_path))
    monkeypatch.setenv("PROJECTBUDGET_DATA_DIR", str(tmp_path / "data"))
    base = ["--database-url", migrated_url]
    runner.invoke(
        app, [*base, "init", "--username", "sean", "--password-stdin"], input=OWNER_PASSWORD + "\n"
    )
    (tmp_path / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    engine = make_engine(migrated_url)
    try:
        factory = make_session_factory(engine)
        source = sync.create_source(
            factory,
            auth.owner_principal(factory),
            sync.SourceCreate(SourceKind.FILE, "shed.yaml"),
            import_dir=tmp_path,
        )
    finally:
        engine.dispose()
    return base, source.id, tmp_path


def test_dry_run_then_apply_then_noop(env: tuple[list[str], str, Path]) -> None:
    base, source_id, _tmp_path = env
    dry = runner.invoke(app, [*base, "sync", source_id, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "dry run, nothing applied" in dry.output
    assert "creates: 2" in dry.output

    applied = runner.invoke(app, [*base, "sync", source_id])
    assert applied.exit_code == 0, applied.output
    assert applied.output.startswith("Applied ")
    assert "creates: 2" in applied.output

    again = runner.invoke(app, [*base, "sync", source_id])
    assert again.exit_code == 0, again.output
    assert "Nothing to do" in again.output

    forced = runner.invoke(app, [*base, "sync", source_id, "--dry-run", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "unchanged: 2" in forced.output


def test_rejected_manifest_and_unknown_source(env: tuple[list[str], str, Path]) -> None:
    base, source_id, tmp_path = env
    (tmp_path / "shed.yaml").write_text(
        "version: 1\nproject: {id: shed}\nitems:\n  - {id: a, name: A, qty: 1, estimate: {amount: 1.5}}\n",
        encoding="utf-8",
    )
    rejected = runner.invoke(app, [*base, "sync", source_id])
    assert rejected.exit_code == 1
    assert "Manifest rejected" in rejected.output
    assert "quoted decimal string" in rejected.output
    missing = runner.invoke(app, [*base, "sync", "nope"])
    assert missing.exit_code == 1
    assert "not found" in missing.output
