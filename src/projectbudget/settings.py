"""Runtime configuration.

Every setting comes from the environment (prefix ``PROJECTBUDGET_``) or from a
``.env`` file in the working directory. Secrets never live in the repository
(RFC v3 §7). Defaults are chosen for the zero-infrastructure path: SQLite in
``~/.projectbudget/`` listening on localhost (ADR-001, ADR-010).
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DEFAULT_DATA_DIR = Path.home() / ".projectbudget"
SQLITE_FILENAME = "projectbudget.sqlite"


class Settings(BaseSettings):
    """Process-wide settings. Construct once; pass explicitly to factories."""

    model_config = SettingsConfigDict(
        env_prefix="PROJECTBUDGET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(
        default=DEFAULT_DATA_DIR,
        description="Directory for the SQLite database and other local state.",
    )
    database_url: str | None = Field(
        default=None,
        description=(
            "SQLAlchemy URL. Leave unset for SQLite in data_dir. "
            "Only sqlite and postgresql backends are supported (ADR-001)."
        ),
    )
    database_password: SecretStr | None = Field(
        default=None,
        description=(
            "Database password, kept out of database_url so it needs no URL-encoding "
            "and can come from a secret store. Replaces any password in the URL."
        ),
    )
    host: str = Field(default="127.0.0.1", description="Interface the HTTP server binds to.")
    port: int = Field(default=8000, ge=1, le=65535, description="Port the HTTP server binds to.")

    @property
    def effective_database_url(self) -> str:
        """The configured URL with the separate password applied, or the default SQLite file."""
        if not self.database_url:
            return sqlite_url(self.data_dir / SQLITE_FILENAME)
        if self.database_password is None:
            return self.database_url
        url = make_url(self.database_url).set(password=self.database_password.get_secret_value())
        return url.render_as_string(hide_password=False)


def sqlite_url(path: Path) -> str:
    """Build a SQLAlchemy URL for a SQLite file, portable across Windows and POSIX."""
    return f"sqlite+pysqlite:///{path.expanduser().as_posix()}"
