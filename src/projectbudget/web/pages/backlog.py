"""The consolidated backlog page: the product's home (ADR-004).

JSON twin: ``GET /api/v1/backlog`` and ``PUT /api/v1/backlog/order``.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.consolidation import Backlog
from projectbudget.domain.principal import Principal
from projectbudget.services import backlog, projects
from projectbudget.web.auth import Owner, is_htmx, session_factory
from projectbudget.web.templates import render

BACKLOG_PATH = "/backlog"

router = APIRouter(tags=["pages"])

ASSUMPTIONS = (
    "Time overlap between projects is not modeled: one set is assumed to move between them.",
    "Shared is the default for reusable items; a requirement can be marked exclusive instead.",
    "Permanently installed parts are better modeled as consumable.",
    "On-hand counts every unit regardless of which project last used it.",
)


def _context(request: Request, principal: Principal) -> dict[str, Any]:
    factory = session_factory(request)
    current: Backlog = backlog.get_backlog(factory, principal)
    names = {
        p.id: p.name for p in projects.list_projects(factory, principal, include_archived=True)
    }
    return {"backlog": current, "project_names": names, "assumptions": ASSUMPTIONS}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get(BACKLOG_PATH, response_class=HTMLResponse)
def backlog_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "backlog.html", _context(request, principal))


@router.post(BACKLOG_PATH + "/{item_id}/move")
def move_row(
    request: Request,
    principal: Owner,
    item_id: str,
    direction: Annotated[Literal["up", "down"], Form()],
) -> Response:
    backlog.move(session_factory(request), principal, item_id, up=direction == "up")
    if is_htmx(request):
        return render(request, "partials/backlog_table.html", _context(request, principal))
    return RedirectResponse(BACKLOG_PATH, status_code=303)
