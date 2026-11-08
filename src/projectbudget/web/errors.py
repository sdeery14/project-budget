"""Turn auth exceptions into the right answer for the caller (JSON, HTMX or a page)."""

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from projectbudget.services.errors import CONFLICT_ERRORS, VALIDATION_ERRORS
from projectbudget.services.guards import NotFoundError, ReadOnlyPrincipalError
from projectbudget.web.auth import (
    LOGIN_PATH,
    AuthenticationRequiredError,
    CsrfError,
    ReadOnlyTokenError,
    SetupRequiredError,
    is_htmx,
)
from projectbudget.web.pages.login import render_login
from projectbudget.web.templates import render

SETUP_MESSAGE = "Setup required: run `projectbudget init` to create the owner."
CSRF_MESSAGE = "Request rejected: missing or invalid CSRF token."
READ_ONLY_MESSAGE = "This API token is read-only."


def wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _error(request: Request, message: str, status_code: int) -> Response:
    if wants_json(request):
        return JSONResponse({"detail": message}, status_code=status_code)
    return render(
        request,
        "error.html",
        {"message": message, "status_code": status_code},
        status_code=status_code,
    )


def authentication_required(request: Request, _exc: Exception) -> Response:
    if wants_json(request):
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if is_htmx(request):
        return Response(status_code=401, headers={"HX-Redirect": LOGIN_PATH})
    next_path = "/"
    if request.method == "GET":
        next_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return render_login(request, next_path=next_path, status_code=401)


def setup_required(request: Request, _exc: Exception) -> Response:
    if wants_json(request):
        return JSONResponse({"detail": SETUP_MESSAGE}, status_code=503)
    return render(request, "setup_required.html", status_code=503)


def csrf_rejected(request: Request, _exc: Exception) -> Response:
    return _error(request, CSRF_MESSAGE, 403)


def read_only_token(request: Request, _exc: Exception) -> Response:
    return _error(request, READ_ONLY_MESSAGE, 403)


def not_found(request: Request, exc: Exception) -> Response:
    return _error(request, str(exc), 404)


def read_only_principal(request: Request, _exc: Exception) -> Response:
    return _error(request, READ_ONLY_MESSAGE, 403)


def invalid_input(request: Request, exc: Exception) -> Response:
    return _error(request, str(exc), 422)


def conflict(request: Request, exc: Exception) -> Response:
    return _error(request, str(exc), 409)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AuthenticationRequiredError, authentication_required)
    app.add_exception_handler(SetupRequiredError, setup_required)
    app.add_exception_handler(CsrfError, csrf_rejected)
    app.add_exception_handler(ReadOnlyTokenError, read_only_token)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ReadOnlyPrincipalError, read_only_principal)
    for error in VALIDATION_ERRORS:
        app.add_exception_handler(error, invalid_input)
    for error in CONFLICT_ERRORS:
        app.add_exception_handler(error, conflict)
