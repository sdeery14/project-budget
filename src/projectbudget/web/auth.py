"""``require_owner``: the one dependency every router except health, login and static uses.

Resolves a bearer token or a session cookie into a :class:`Principal`, and for
state-changing requests made with a cookie, checks the per-session CSRF token
(ADR-005). The Origin check lives in :mod:`projectbudget.web.security` because it
must run before any route, including login.
"""

from datetime import timedelta
from typing import Annotated, cast

from fastapi import Depends, Request, Response

from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import auth, tokens
from projectbudget.settings import Settings

SESSION_COOKIE = "pb_session"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORM_CONTENT_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})
LOGIN_PATH = "/login"


class AuthenticationRequiredError(Exception):
    """No valid session or token; answer 401."""


class SetupRequiredError(Exception):
    """No owner exists yet; the app refuses to serve until ``projectbudget init`` runs."""


class CsrfError(Exception):
    """A cookie-authenticated state change without a valid CSRF token; answer 403."""


class ReadOnlyTokenError(Exception):
    """A read-only API token attempted a state change; answer 403."""


def session_factory(request: Request) -> SessionFactory:
    return cast(SessionFactory, request.app.state.session_factory)


def app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def safe_next(path: str | None) -> str:
    """Only same-origin relative paths are followed after login."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


async def require_owner(request: Request) -> Principal:
    factory = session_factory(request)
    settings = app_settings(request)

    secret = bearer_token(request)
    if secret is not None:
        principal = tokens.resolve_token(factory, secret)
        if principal is None:
            raise AuthenticationRequiredError
        if request.method not in SAFE_METHODS and principal.read_only:
            raise ReadOnlyTokenError
        request.state.principal = principal
        return principal

    cookie = request.cookies.get(SESSION_COOKIE)
    resolved = (
        auth.resolve_session(
            factory,
            cookie,
            idle_timeout=timedelta(minutes=settings.session_idle_timeout_minutes),
        )
        if cookie
        else None
    )
    if resolved is None:
        if not auth.owner_exists(factory):
            raise SetupRequiredError
        raise AuthenticationRequiredError

    request.state.principal = resolved.principal
    request.state.session_id = resolved.id
    request.state.csrf_token = resolved.csrf_token
    if request.method not in SAFE_METHODS:
        provided = await _csrf_token_from(request)
        if provided is None or not auth.secrets_match(resolved.csrf_token, provided):
            raise CsrfError
    return resolved.principal


async def _csrf_token_from(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type in FORM_CONTENT_TYPES:
        form = await request.form()
        value = form.get(CSRF_FIELD)
        return value if isinstance(value, str) else None
    return None


def set_session_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=int(auth.SESSION_MAX_AGE.total_seconds()),
        path="/",
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )


Owner = Annotated[Principal, Depends(require_owner)]
"""Route parameter type: the authenticated owner. Also used as a router-level dependency."""
