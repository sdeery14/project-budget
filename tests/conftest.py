"""Shared fixtures.

``PROJECTBUDGET_TEST_DATABASE_URL`` selects the backend; CI sets it to SQLite in
one job and PostgreSQL in another (ADR-001). Locally it defaults to a SQLite file
in a temporary directory. Tests that touch the schema leave it as they found it;
tests that write rows use ``migrated_url`` and get the tables emptied afterwards.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from projectbudget.domain.principal import Principal
from projectbudget.persistence import migrate, models  # noqa: F401  (registers tables)
from projectbudget.persistence.base import Base
from projectbudget.persistence.engine import backend_name, make_engine
from projectbudget.persistence.session import SessionFactory, make_session_factory
from projectbudget.services import auth
from projectbudget.settings import Settings, sqlite_url
from projectbudget.web.app import create_app

TEST_DATABASE_URL_ENV = "PROJECTBUDGET_TEST_DATABASE_URL"

OWNER_USERNAME = "owner"
OWNER_PASSWORD = "correct horse battery staple"
ORIGIN = "https://testserver"


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if url:
        return url
    return sqlite_url(tmp_path_factory.mktemp("db") / "test.sqlite")


@pytest.fixture(scope="session")
def backend(database_url: str) -> str:
    return backend_name(database_url)


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(database_url=database_url, data_dir=tmp_path)


@pytest.fixture
def migrated_url(database_url: str) -> Iterator[str]:
    """The shared database at head, emptied again after the test."""
    migrate.upgrade(database_url, "head")
    yield database_url
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(migrated_url: str) -> Iterator[SessionFactory]:
    engine = make_engine(migrated_url)
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture
def app_settings(migrated_url: str, tmp_path: Path) -> Settings:
    return Settings(database_url=migrated_url, data_dir=tmp_path)


def make_client(settings: Settings, *, base_url: str = ORIGIN) -> TestClient:
    """A browser-like client: same-origin header on every request, redirects not followed."""
    return TestClient(
        create_app(settings),
        base_url=base_url,
        headers={"Origin": base_url},
        follow_redirects=False,
    )


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    with make_client(app_settings) as client:
        yield client


@pytest.fixture
def owner(session_factory: SessionFactory) -> auth.OwnerInfo:
    return auth.create_owner(session_factory, OWNER_USERNAME, OWNER_PASSWORD)


@pytest.fixture
def principal(owner: auth.OwnerInfo) -> Principal:
    """The owner acting through a browser session: may read and write."""
    return Principal(owner_id=owner.id, username=owner.username, auth="session")


@pytest.fixture
def read_only_principal(owner: auth.OwnerInfo) -> Principal:
    """The owner acting through a read-only API token."""
    return Principal(owner_id=owner.id, username=owner.username, auth="token", read_only=True)


def log_in(client: TestClient) -> None:
    response = client.post("/login", data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD})
    assert response.status_code == 303, response.text


@pytest.fixture
def logged_in(client: TestClient, owner: auth.OwnerInfo) -> TestClient:
    log_in(client)
    return client


def csrf_token_from(html: str) -> str:
    marker = 'name="csrf_token" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]
