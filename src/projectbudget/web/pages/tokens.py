"""API token management page and its HTMX fragment (ADR-005).

JSON twin: ``/api/v1/tokens`` (RFC v3 §3, every state change has one).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.principal import Principal
from projectbudget.services import tokens
from projectbudget.web.auth import is_htmx, require_owner, session_factory
from projectbudget.web.templates import render

TOKENS_PATH = "/settings/tokens"

router = APIRouter(prefix=TOKENS_PATH, tags=["tokens"])


def _page(
    request: Request,
    principal: Principal,
    *,
    issued: tokens.IssuedToken | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "tokens.html",
        {
            "tokens": tokens.list_tokens(session_factory(request), principal),
            "issued": issued,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def tokens_page(
    request: Request, principal: Annotated[Principal, Depends(require_owner)]
) -> HTMLResponse:
    return _page(request, principal)


@router.post("", response_class=HTMLResponse)
def create_token(
    request: Request,
    principal: Annotated[Principal, Depends(require_owner)],
    name: Annotated[str, Form()],
    read_only: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        issued = tokens.issue_token(session_factory(request), principal, name, read_only=read_only)
    except tokens.InvalidTokenNameError as exc:
        return _page(request, principal, error=str(exc), status_code=422)
    except tokens.TokenManagementRequiresSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _page(request, principal, issued=issued)


@router.post("/{token_id}/revoke")
def revoke_token(
    request: Request,
    token_id: str,
    principal: Annotated[Principal, Depends(require_owner)],
) -> Response:
    try:
        info = tokens.revoke_token(session_factory(request), principal, token_id)
    except tokens.TokenManagementRequiresSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if info is None:
        raise HTTPException(status_code=404, detail="No such token")
    if is_htmx(request):
        return render(request, "partials/token_row.html", {"token": info})
    return RedirectResponse(TOKENS_PATH, status_code=303)
