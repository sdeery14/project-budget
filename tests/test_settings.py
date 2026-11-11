"""Settings resolution, in particular the separate database password."""

from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from projectbudget.settings import SQLITE_FILENAME, Settings


def test_default_is_sqlite_in_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROJECTBUDGET_DATABASE_URL", raising=False)
    monkeypatch.delenv("PROJECTBUDGET_DATABASE_PASSWORD", raising=False)
    settings = Settings(data_dir=tmp_path)
    url = make_url(settings.effective_database_url)
    assert url.get_backend_name() == "sqlite"
    assert url.database is not None
    assert url.database.endswith(SQLITE_FILENAME)


def test_url_without_separate_password_is_used_verbatim(tmp_path: Path) -> None:
    raw = "postgresql+psycopg://u:p@h:5432/db"
    assert Settings(database_url=raw, data_dir=tmp_path).effective_database_url == raw


@pytest.mark.parametrize(
    "password",
    ["abc@123", "p@ss:w/rd?#&=%", "spaces and +plus", "ünïcödé"],
)
def test_separate_password_is_encoded_and_round_trips(password: str, tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://projectbudget@db:5432/projectbudget",
        database_password=SecretStr(password),
        data_dir=tmp_path,
    )
    url = make_url(settings.effective_database_url)
    assert url.password == password
    assert url.username == "projectbudget"
    assert url.host == "db"
    assert url.port == 5432
    assert url.database == "projectbudget"


def test_separate_password_replaces_password_in_url(tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://u:old@h/db",
        database_password=SecretStr("new@one"),
        data_dir=tmp_path,
    )
    assert make_url(settings.effective_database_url).password == "new@one"


def test_password_is_not_shown_in_repr(tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://u@h/db",
        database_password=SecretStr("s3cret"),
        data_dir=tmp_path,
    )
    assert "s3cret" not in repr(settings)


def test_github_token_comes_from_the_environment_and_stays_out_of_repr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROJECTBUDGET_GITHUB_TOKEN", raising=False)
    assert Settings(data_dir=tmp_path).effective_github_token is None
    monkeypatch.setenv("PROJECTBUDGET_GITHUB_TOKEN", "github_pat_secret")
    settings = Settings(data_dir=tmp_path)
    assert settings.effective_github_token == "github_pat_secret"
    assert "github_pat_secret" not in repr(settings)
    assert "github_pat_secret" not in str(settings)
