"""``projectbudget`` CLI: ``init``, ``run``, ``db upgrade|downgrade|current|revision``, ``version``.

``export``, ``import`` and ``sync`` arrive with their milestones.
The container entrypoint runs ``db upgrade`` and then ``run`` (ADR-010).
"""

import os
import sys
from typing import Annotated

import typer
import uvicorn

from projectbudget import __version__
from projectbudget.domain.money import InvalidCurrencyError, validate_currency
from projectbudget.persistence import migrate
from projectbudget.persistence.engine import UnsupportedDatabaseError, backend_name, make_engine
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, passwords, workspace
from projectbudget.settings import Settings

app = typer.Typer(
    name="projectbudget",
    help="One honest shopping backlog across all your projects.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="Database migrations (Alembic).", no_args_is_help=True)
app.add_typer(db_app, name="db")

DATABASE_URL_ENV = "PROJECTBUDGET_DATABASE_URL"


@app.callback()
def main(
    ctx: typer.Context,
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar=DATABASE_URL_ENV,
            help="SQLAlchemy URL; sqlite and postgresql only. Default: SQLite in the data dir.",
            show_default=False,
        ),
    ] = None,
) -> None:
    # An explicit option beats the environment and .env; nothing is written back to
    # os.environ here, so the override cannot leak into other code or tests.
    settings = Settings(database_url=database_url) if database_url else Settings()
    try:
        backend_name(settings.effective_database_url)
    except UnsupportedDatabaseError as exc:
        raise typer.BadParameter(str(exc), param_hint="--database-url") from exc
    ctx.obj = settings


def _settings(ctx: typer.Context) -> Settings:
    settings: Settings = ctx.obj
    return settings


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def init(
    ctx: typer.Context,
    username: Annotated[
        str, typer.Option(prompt=True, help="Owner username (stored lower-case, no spaces).")
    ],
    password_stdin: Annotated[
        bool,
        typer.Option(
            "--password-stdin",
            help="Read the password from standard input instead of prompting (for scripts).",
        ),
    ] = False,
    currency: Annotated[
        str,
        typer.Option(help="Workspace currency as an ISO 4217 code; fixed after init (ADR-003)."),
    ] = workspace.DEFAULT_CURRENCY,
) -> None:
    """Create the owner account and set the workspace currency. Applies pending migrations first."""
    settings = _settings(ctx)
    url = settings.effective_database_url
    try:
        currency_code = validate_currency(currency)
    except InvalidCurrencyError as exc:
        raise typer.BadParameter(str(exc), param_hint="--currency") from exc
    migrate.upgrade(url)
    engine = make_engine(url)
    try:
        factory = make_session_factory(engine)
        if auth.owner_exists(factory):
            typer.echo("An owner already exists; this instance is initialised.", err=True)
            raise typer.Exit(code=1)
        if password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = typer.prompt(
                f"Password (at least {passwords.MIN_PASSWORD_LENGTH} characters)",
                hide_input=True,
                confirmation_prompt=True,
            )
        try:
            owner = auth.create_owner(factory, username, password)
        except (auth.InvalidUsernameError, passwords.WeakPasswordError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        workspace.set_currency(factory, currency_code)
    finally:
        engine.dispose()
    typer.echo(f"Owner {owner.username!r} created. Workspace currency: {currency_code}.")
    typer.echo(
        f"Start the app with `projectbudget run` and sign in at "
        f"http://{settings.host}:{settings.port}/login"
    )


@app.command()
def run(
    ctx: typer.Context,
    host: Annotated[
        str | None, typer.Option(help="Interface to bind; default from settings.")
    ] = None,
    port: Annotated[int | None, typer.Option(help="Port to bind; default from settings.")] = None,
    reload: Annotated[
        bool, typer.Option(help="Restart on code changes (development only).")
    ] = False,
) -> None:
    """Start the HTTP server with one worker (ADR-002, ADR-010)."""
    settings = _settings(ctx)
    # uvicorn --factory builds the app in its own process (always so with --reload), and
    # that process reads settings from the environment, so hand it the resolved URL.
    os.environ[DATABASE_URL_ENV] = settings.effective_database_url
    uvicorn.run(
        "projectbudget.web.app:create_app",
        factory=True,
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        workers=1,
        proxy_headers=True,
        log_level="info",
    )


@db_app.command("upgrade")
def db_upgrade(
    ctx: typer.Context,
    revision: Annotated[str, typer.Argument(help="Target revision.")] = "head",
) -> None:
    """Apply migrations up to a revision (default: head)."""
    url = _settings(ctx).effective_database_url
    migrate.upgrade(url, revision)
    typer.echo(f"Database at revision {migrate.current_revision(url) or 'base'}")


@db_app.command("downgrade")
def db_downgrade(
    ctx: typer.Context,
    revision: Annotated[str, typer.Argument(help="Target revision, e.g. -1 or base.")],
) -> None:
    """Revert migrations down to a revision."""
    url = _settings(ctx).effective_database_url
    migrate.downgrade(url, revision)
    typer.echo(f"Database at revision {migrate.current_revision(url) or 'base'}")


@db_app.command("current")
def db_current(ctx: typer.Context) -> None:
    """Show the database revision and the newest packaged revision."""
    url = _settings(ctx).effective_database_url
    current = migrate.current_revision(url) or "base"
    head = migrate.head_revision() or "base"
    suffix = "" if current == head else f" (head is {head}; run `projectbudget db upgrade`)"
    typer.echo(f"{current}{suffix}")


@db_app.command("revision")
def db_revision(
    ctx: typer.Context,
    message: Annotated[str, typer.Option("--message", "-m", help="Short description.")],
    autogenerate: Annotated[
        bool, typer.Option(help="Diff the models against the database to draft the migration.")
    ] = True,
) -> None:
    """Create a new migration file with the next sequential id (development only)."""
    url = _settings(ctx).effective_database_url
    migrate.create_revision(url, message, autogenerate=autogenerate)
    typer.echo(f"Created revision {migrate.head_revision()}")
