"""Login and logout (ADR-005).

``/login`` is one of the three places that do not require the owner. It still
sits behind the Origin check and the database-backed rate limit.
"""

import math
from collections.abc import Mapping
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.principal import Principal
from projectbudget.services import auth
from projectbudget.web.auth import (
    LOGIN_PATH,
    SESSION_COOKIE,
    SetupRequiredError,
    app_settings,
    clear_session_cookie,
    require_owner,
    safe_next,
    session_factory,
    set_session_cookie,
)
from projectbudget.web.templates import render

router = APIRouter(tags=["auth"])

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
BAD_CREDENTIALS = "Wrong username or password."


def render_login(
    request: Request,
    *,
    next_path: str,
    username: str = "",
    error: str | None = None,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    settings = app_settings(request)
    plain_http = (
        settings.secure_cookies
        and request.url.scheme == "http"
        and (request.url.hostname or "") not in LOOPBACK_HOSTS
    )
    return render(
        request,
        "login.html",
        {
            "next": safe_next(next_path),
            "username": username,
            "error": error,
            "plain_http_warning": plain_http,
        },
        status_code=status_code,
        headers=headers,
    )


@router.get(LOGIN_PATH, response_class=HTMLResponse)
def login_page(
    request: Request,
    next_path: Annotated[str, Query(alias="next")] = "/",
) -> Response:
    factory = session_factory(request)
    if not auth.owner_exists(factory):
        raise SetupRequiredError
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        settings = app_settings(request)
        idle = timedelta(minutes=settings.session_idle_timeout_minutes)
        if auth.resolve_session(factory, cookie, idle_timeout=idle) is not None:
            return RedirectResponse(safe_next(next_path), status_code=303)
    return render_login(request, next_path=next_path)


@router.post(LOGIN_PATH, response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_path: Annotated[str, Form(alias="next")] = "/",
) -> Response:
    factory = session_factory(request)
    settings = app_settings(request)
    if not auth.owner_exists(factory):
        raise SetupRequiredError

    client_host = request.client.host if request.client else "unknown"
    limit_keys = [
        f"user:{username.strip().lower()[: auth.USERNAME_MAX_LENGTH]}",
        f"ip:{client_host}",
    ]
    try:
        owner = auth.authenticate(factory, username, password, limit_keys=limit_keys)
    except auth.LoginLockedError as exc:
        seconds = max(1, math.ceil(exc.retry_after.total_seconds()))
        minutes = max(1, math.ceil(seconds / 60))
        return render_login(
            request,
            next_path=next_path,
            username=username,
            error=f"Too many failed attempts. Try again in {minutes} minute(s).",
            status_code=429,
            headers={"Retry-After": str(seconds)},
        )
    if owner is None:
        return render_login(
            request, next_path=next_path, username=username, error=BAD_CREDENTIALS, status_code=401
        )

    # Rotate: whatever session the browser presented is gone, a fresh one replaces it.
    old_cookie = request.cookies.get(SESSION_COOKIE)
    if old_cookie:
        auth.revoke_session_by_cookie(factory, old_cookie)
    new_session = auth.create_session(factory, owner.id)
    response: Response = RedirectResponse(safe_next(next_path), status_code=303)
    set_session_cookie(response, new_session.cookie_value, settings)
    return response


@router.post("/logout")
def logout(
    request: Request,
    _principal: Annotated[Principal, Depends(require_owner)],
) -> Response:
    session_id = getattr(request.state, "session_id", None)
    if isinstance(session_id, str):
        auth.revoke_session(session_factory(request), session_id)
    response: Response = RedirectResponse(LOGIN_PATH, status_code=303)
    clear_session_cookie(response, app_settings(request))
    return response
