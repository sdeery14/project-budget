"""The owner's account page: change the password (#12).

No JSON twin on purpose: like token management, a password changes only from a browser
session (or with ``projectbudget passwd`` on the host), never with an API token.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from projectbudget.domain.principal import Principal
from projectbudget.services import auth, passwords
from projectbudget.web.auth import require_owner, session_factory
from projectbudget.web.templates import render

ACCOUNT_PATH = "/settings/account"

router = APIRouter(prefix=ACCOUNT_PATH, tags=["account"])


def _page(
    request: Request,
    principal: Principal,
    *,
    changed: bool = False,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "account.html",
        {
            "changed": changed,
            "error": error,
            "min_length": passwords.MIN_PASSWORD_LENGTH,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def account_page(
    request: Request,
    principal: Annotated[Principal, Depends(require_owner)],
    changed: bool = False,
) -> HTMLResponse:
    """The account page; ``?changed=1`` is where a successful password change lands (#69)."""
    return _page(request, principal, changed=changed)


@router.get("/password", include_in_schema=False)
def password_form_address(
    principal: Annotated[Principal, Depends(require_owner)],
) -> RedirectResponse:
    """The change form posts here; a re-opened address goes back to the account page (#69)."""
    return RedirectResponse(ACCOUNT_PATH, status_code=303)


@router.post("/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    principal: Annotated[Principal, Depends(require_owner)],
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
) -> Response:
    if new_password != confirm_password:
        return _page(
            request,
            principal,
            error="The new password was not typed the same twice",
            status_code=422,
        )
    try:
        auth.change_password(
            session_factory(request),
            principal,
            current_password,
            new_password,
            keep_session_id=getattr(request.state, "session_id", None),
        )
    except auth.WrongPasswordError:
        return _page(request, principal, error="The current password is wrong", status_code=422)
    except passwords.WeakPasswordError as exc:
        return _page(request, principal, error=str(exc), status_code=422)
    except auth.PasswordChangeRequiresSessionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Redirect after the write (#69): a refresh must not resubmit the form.
    return RedirectResponse(f"{ACCOUNT_PATH}?changed=1", status_code=303)
