"""Browser smoke tests: a real server, a real Chromium, the pages as the owner sees them.

Collected only when ``pytest-playwright`` is installed (``uv sync --group e2e`` and
``uv run playwright install chromium``); otherwise the whole directory is skipped so the
unit suite stays zero-setup. CI runs it as the "UI smoke (Playwright)" job.
"""

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

pytest.importorskip(
    "pytest_playwright", reason="pytest-playwright is not installed (uv sync --group e2e)"
)

from playwright.sync_api import Browser, BrowserContext, Error, Page

from projectbudget.persistence import migrate
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth
from projectbudget.settings import Settings, sqlite_url
from projectbudget.web.app import create_app

USERNAME = "smoke"
PASSWORD = "correct horse battery staple"
PHONE_WIDTH = 320
DESKTOP = {"width": 1280, "height": 900}


@dataclass(frozen=True)
class LiveServer:
    url: str
    username: str
    password: str
    import_dir: Path
    """Where file sync sources are read from; tests may drop manifests here."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    """One migrated SQLite workspace with an owner, served by uvicorn in a thread."""
    data_dir: Path = tmp_path_factory.mktemp("e2e")
    url = sqlite_url(data_dir / "projectbudget.sqlite")
    migrate.upgrade(url)
    engine = make_engine(url)
    try:
        auth.create_owner(make_session_factory(engine), USERNAME, PASSWORD)
    finally:
        engine.dispose()

    port = _free_port()
    settings = Settings(database_url=url, data_dir=data_dir, host="127.0.0.1", port=port)
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the e2e server did not start")
        time.sleep(0.05)
    try:
        yield LiveServer(
            url=f"http://127.0.0.1:{port}",
            username=USERNAME,
            password=PASSWORD,
            import_dir=settings.effective_sync_import_dir,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, object]) -> dict[str, object]:
    return {**browser_context_args, "viewport": DESKTOP}


@pytest.fixture(scope="session")
def browser(browser: Browser) -> Browser:
    """Fail with a clear message when the Chromium build is missing."""
    try:
        browser.new_context().close()
    except Error as exc:  # pragma: no cover - only without the browser download
        pytest.skip(f"Chromium is not installed for Playwright: {exc}")
    return browser


@pytest.fixture
def signed_in(page: Page, live_server: LiveServer) -> Page:
    """A page whose browser session is signed in as the owner."""
    page.goto(f"{live_server.url}/login")
    page.get_by_label("Username").fill(live_server.username)
    page.get_by_label("Password").fill(live_server.password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{live_server.url}/")
    return page


@pytest.fixture
def phone(context: BrowserContext, signed_in: Page) -> Page:
    """The same signed-in session at phone width."""
    signed_in.set_viewport_size({"width": PHONE_WIDTH, "height": 700})
    return signed_in
