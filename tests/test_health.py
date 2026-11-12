"""``/health`` reports the version and real database reachability."""

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget import __version__
from projectbudget.settings import Settings, sqlite_url
from projectbudget.web.app import create_app


def test_health_ok_when_database_answers(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": __version__}


def test_health_503_when_database_unreachable(tmp_path: Path) -> None:
    # SQLite cannot open a directory as a database on any platform, so SELECT 1 fails.
    not_a_file = tmp_path / "is_a_directory"
    not_a_file.mkdir()
    bad = Settings(database_url=sqlite_url(not_a_file), data_dir=tmp_path)
    with TestClient(create_app(bad)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


def test_favicon_is_public_and_every_page_links_it(logged_in: TestClient) -> None:
    """#16: no more 404 in the console on every page, and the tab has an icon."""
    icon = logged_in.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.text.startswith("<svg")
    linked = logged_in.get("/static/favicon.svg")
    assert linked.status_code == 200
    assert linked.text == icon.text
    # Every page links the SVG, signed in or not; the owner's pages and the sign-in form.
    for path in ("/backlog", "/logout"):
        if path == "/logout":
            logged_in.post(path, data={"csrf_token": csrf_token_from(logged_in.get("/").text)})
            path = "/backlog"  # now answers 401 with the sign-in form in place
        page = logged_in.get(path)
        assert 'rel="icon"' in page.text, page.status_code
        assert "/static/favicon.svg?v=" in page.text


def test_interactive_docs_are_off_until_auth_exists(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
