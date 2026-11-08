"""``projectbudget run --demo``: a throwaway SQLite workspace seeded with fictional data (RFC v3 §3).

The demo never touches the real data directory. It creates a fresh temporary
directory, migrates a SQLite file there, creates an owner called ``demo`` with a
random password that is printed once, and seeds the canonical workspace.
"""

import secrets
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import seed_canonical
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth
from projectbudget.services.clock import today
from projectbudget.settings import sqlite_url

DEMO_USERNAME = "demo"
DEMO_PASSWORD_BYTES = 12  # 16 url-safe characters, well above the minimum length


@dataclass(frozen=True, slots=True)
class DemoWorkspace:
    directory: Path
    database_url: str
    username: str
    password: str


def prepare_demo(directory: Path | None = None, *, password: str | None = None) -> DemoWorkspace:
    """Build the demo database. ``directory`` defaults to a new temporary folder."""
    root = directory or Path(tempfile.mkdtemp(prefix="projectbudget-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    url = sqlite_url(root / "demo.sqlite")
    secret = password or secrets.token_urlsafe(DEMO_PASSWORD_BYTES)
    migrate.upgrade(url)
    engine = make_engine(url)
    try:
        factory = make_session_factory(engine)
        owner = auth.create_owner(factory, DEMO_USERNAME, secret)
        principal = Principal(owner_id=owner.id, username=owner.username, auth="session")
        # Balance entered a week ago so the "balance may be stale" guard stays quiet for a while.
        seed_canonical(factory, principal, as_of=today() - timedelta(days=7))
    finally:
        engine.dispose()
    return DemoWorkspace(directory=root, database_url=url, username=DEMO_USERNAME, password=secret)
