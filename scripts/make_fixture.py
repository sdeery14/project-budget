"""Regenerate ``tests/fixtures/workspace.json`` from the canonical seed (ADR-008).

Run with ``uv run python scripts/make_fixture.py`` after changing the canonical
workspace. The seed runs against a throwaway SQLite file under a frozen clock;
ids are fresh each time, which is fine: the file is the fixture, not the seed.
"""

import tempfile
from datetime import date, datetime
from pathlib import Path

from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import seed_canonical
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import transfer
from projectbudget.settings import sqlite_url

FROZEN_NOW = datetime(2026, 11, 8, 9, 0, 0)
FROZEN_AS_OF = date(2026, 11, 1)
TARGET = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "workspace.json"
SEED_PRINCIPAL = Principal(owner_id="fixture", username="fixture", auth="session")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        url = sqlite_url(Path(tmp) / "fixture.sqlite")
        migrate.upgrade(url)
        engine = make_engine(url)
        try:
            factory = make_session_factory(engine)
            seed_canonical(factory, SEED_PRINCIPAL, now=FROZEN_NOW, as_of=FROZEN_AS_OF)
            document = transfer.export_workspace(factory, SEED_PRINCIPAL, now=FROZEN_NOW)
        finally:
            engine.dispose()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(document.to_json(), encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(TARGET.parent.parent.parent)}")  # noqa: T201


if __name__ == "__main__":
    main()
