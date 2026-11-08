"""The landing page. The backlog replaces it in PR4b (ADR-004)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from projectbudget.domain.principal import Principal
from projectbudget.web.auth import require_owner
from projectbudget.web.templates import render

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request, principal: Annotated[Principal, Depends(require_owner)]) -> HTMLResponse:
    return render(request, "home.html", {"username": principal.username})
