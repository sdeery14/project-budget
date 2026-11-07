"""``/health`` reports the version and real database reachability."""

from pathlib import Path

from fastapi.testclient import TestClient

from projectbudget import __version__
from projectbudget.settings import Settings, sqlite_url
from projectbudget.web.app import create_app


def test_health_ok_when_database_answers(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": __version__}


def test_health_503_when_database_unreachable(tmp_path: Path) -> None:
    # A read-only, non-existent SQLite file cannot be opened, so SELECT 1 fails.
    missing = tmp_path / "missing" / "nope.sqlite"
    bad = Settings(database_url=f"{sqlite_url(missing)}?mode=ro&uri=true", data_dir=tmp_path)
    with TestClient(create_app(bad)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


def test_interactive_docs_are_off_until_auth_exists(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
