"""Shared fixtures.

``PROJECTBUDGET_TEST_DATABASE_URL`` selects the backend; CI sets it to SQLite in
one job and PostgreSQL in another (ADR-001). Locally it defaults to a SQLite file
in a temporary directory. Tests that touch the schema leave it as they found it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from projectbudget.persistence.engine import backend_name, make_engine
from projectbudget.settings import Settings, sqlite_url

TEST_DATABASE_URL_ENV = "PROJECTBUDGET_TEST_DATABASE_URL"


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
