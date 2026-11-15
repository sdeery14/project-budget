"""Projects and their requirements: list, detail, edit, add, change intent, consume.

JSON twins: ``/api/v1/projects`` and ``/api/v1/requirements``.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from projectbudget.domain.catalog import Intent, ItemKind, Sharing
from projectbudget.domain.principal import Principal
from projectbudget.services import catalog, inventory, projects, requirements, sync
from projectbudget.services.catalog import CatalogItemInfo
from projectbudget.services.errors import CONFLICT_ERRORS, USER_ERRORS
from projectbudget.services.requirements import RequirementInfo
from projectbudget.web.auth import Owner, is_htmx, session_factory
from projectbudget.web.templates import render

PROJECTS_PATH = "/projects"

router = APIRouter(tags=["pages"])


@dataclass(frozen=True, slots=True)
class RequirementRow:
    """One requirement with what the row needs to render its actions."""

    requirement: RequirementInfo
    item: CatalogItemInfo
    consumed: int
    on_hand: int
    project_synced: bool = False
    """Whether the project follows a sync source; decides the "manual" badge (#35)."""

    @property
    def manual(self) -> bool:
        """Entered by hand on a synced project: the manifest does not know this row (ADR-012)."""
        return self.project_synced and self.requirement.source_id is None

    @property
    def remaining(self) -> int:
        return max(self.requirement.qty - self.consumed, 0)

    @property
    def can_consume(self) -> bool:
        """Active, something left, stock available, and not a shared reusable item (ADR-004)."""
        shared_reusable = (
            self.item.kind is ItemKind.REUSABLE and self.requirement.sharing is Sharing.SHARED
        )
        return (
            self.requirement.intent is Intent.ACTIVE
            and not shared_reusable
            and self.remaining > 0
            and self.on_hand > 0
        )


def _row(
    request: Request, principal: Principal, info: RequirementInfo, *, synced: bool | None = None
) -> RequirementRow:
    factory = session_factory(request)
    if synced is None:
        synced = projects.get_project(factory, principal, info.project_id).source_id is not None
    return RequirementRow(
        requirement=info,
        item=catalog.get_item(factory, principal, info.catalog_item_id),
        consumed=inventory.consumed(factory, principal, info.id),
        on_hand=inventory.on_hand(factory, principal, info.catalog_item_id),
        project_synced=synced,
    )


def _list_context(request: Request, principal: Principal, **extra: Any) -> dict[str, Any]:
    rows = projects.list_projects(session_factory(request), principal, include_archived=True)
    return {"projects": rows, **extra}


def _detail_context(
    request: Request, principal: Principal, project_id: str, **extra: Any
) -> dict[str, Any]:
    factory = session_factory(request)
    project = projects.get_project(factory, principal, project_id)
    infos = requirements.list_requirements(factory, principal, project_id, include_inactive=True)
    source_id = project.source_id
    source = last_applied = None
    if source_id is not None:
        source = sync.get_source(factory, principal, source_id)
        last_applied = sync.last_applied_run(factory, principal, source_id)
    return {
        "project": project,
        "source": source,
        "last_applied": last_applied,
        "rows": [_row(request, principal, info, synced=source is not None) for info in infos],
        "items": catalog.list_items(factory, principal),
        "intents": list(Intent),
        **extra,
    }


@router.get(PROJECTS_PATH, response_class=HTMLResponse)
def projects_page(request: Request, principal: Owner) -> HTMLResponse:
    return render(request, "projects.html", _list_context(request, principal))


@router.post(PROJECTS_PATH, response_class=HTMLResponse)
def create_project(
    request: Request,
    principal: Owner,
    name: Annotated[str, Form()],
    notes: Annotated[str, Form()] = "",
) -> Response:
    try:
        info = projects.create_project(
            session_factory(request), principal, projects.ProjectCreate(name=name, notes=notes)
        )
    except USER_ERRORS as exc:
        return render(
            request,
            "projects.html",
            _list_context(request, principal, error=str(exc)),
            status_code=422,
        )
    return RedirectResponse(f"{PROJECTS_PATH}/{info.id}", status_code=303)


@router.get(PROJECTS_PATH + "/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, principal: Owner, project_id: str) -> HTMLResponse:
    return render(request, "project.html", _detail_context(request, principal, project_id))


@router.post(PROJECTS_PATH + "/{project_id}", response_class=HTMLResponse)
def update_project(
    request: Request,
    principal: Owner,
    project_id: str,
    notes: Annotated[str, Form()] = "",
    name: Annotated[str | None, Form()] = None,
) -> Response:
    """Rename a project and edit its notes (#13). A synced project's form sends no name:
    the manifest owns it (ADR-007), so the page only lets the owner change the notes."""
    try:
        projects.update_project(
            session_factory(request),
            principal,
            project_id,
            projects.ProjectUpdate(name=name, notes=notes),
        )
    except USER_ERRORS as exc:
        return render(
            request,
            "project.html",
            _detail_context(request, principal, project_id, project_error=str(exc)),
            status_code=422,
        )
    return RedirectResponse(f"{PROJECTS_PATH}/{project_id}", status_code=303)


@router.post(PROJECTS_PATH + "/{project_id}/archive")
def archive_project(
    request: Request,
    principal: Owner,
    project_id: str,
    archived: Annotated[bool, Form()],
) -> Response:
    projects.update_project(
        session_factory(request), principal, project_id, projects.ProjectUpdate(archived=archived)
    )
    return RedirectResponse(f"{PROJECTS_PATH}/{project_id}", status_code=303)


@router.post(PROJECTS_PATH + "/{project_id}/requirements", response_class=HTMLResponse)
def add_requirement(
    request: Request,
    principal: Owner,
    project_id: str,
    qty: Annotated[int, Form()],
    item_text: Annotated[str, Form()] = "",
    catalog_item_id: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "",
    priority: Annotated[int, Form()] = 2,
    sharing: Annotated[Sharing, Form()] = Sharing.SHARED,
    notes: Annotated[str, Form()] = "",
) -> Response:
    """Pick an item from the catalog or type one; a typed name creates an unreviewed item."""
    chosen = catalog_item_id.strip() or None
    typed = item_text.strip() or None
    command = requirements.RequirementCreate(
        project_id=project_id,
        qty=qty,
        priority=priority,
        catalog_item_id=chosen,
        item_text=None if chosen else typed,
        unit=unit.strip() or None,
        sharing=sharing,
        notes=notes,
    )
    try:
        requirements.add_requirement(session_factory(request), principal, command)
    except USER_ERRORS as exc:
        return render(
            request,
            "project.html",
            _detail_context(request, principal, project_id, error=str(exc)),
            status_code=422,
        )
    return RedirectResponse(f"{PROJECTS_PATH}/{project_id}", status_code=303)


def _row_or_redirect(request: Request, principal: Principal, info: RequirementInfo) -> Response:
    if is_htmx(request):
        return render(
            request, "partials/requirement_row.html", {"row": _row(request, principal, info)}
        )
    return RedirectResponse(f"{PROJECTS_PATH}/{info.project_id}", status_code=303)


@router.post("/requirements/{requirement_id}/intent")
def set_intent(
    request: Request,
    principal: Owner,
    requirement_id: str,
    intent: Annotated[Intent, Form()],
    archive_reason: Annotated[str, Form()] = "",
) -> Response:
    info = requirements.set_intent(
        session_factory(request),
        principal,
        requirement_id,
        intent,
        archive_reason=archive_reason or None,
    )
    return _row_or_redirect(request, principal, info)


def _row_error(
    request: Request, principal: Principal, requirement_id: str, error: str, status_code: int
) -> Response:
    """The row again with the message inside it over htmx; the whole page otherwise."""
    factory = session_factory(request)
    info = requirements.get_requirement(factory, principal, requirement_id)
    if is_htmx(request):
        return render(
            request,
            "partials/requirement_row.html",
            {"row": _row(request, principal, info), "error": error},
            status_code=status_code,
        )
    return render(
        request,
        "project.html",
        _detail_context(request, principal, info.project_id, error=error),
        status_code=status_code,
    )


@router.post("/requirements/{requirement_id}/edit")
def edit_requirement(
    request: Request,
    principal: Owner,
    requirement_id: str,
    qty: Annotated[int | None, Form()] = None,
    priority: Annotated[int | None, Form()] = None,
    sharing: Annotated[Sharing | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
) -> Response:
    """Change quantity, priority, sharing or notes in place (#14); the JSON twin is PATCH.

    A field the form did not send keeps its value: a manifest-managed row's form sends only
    sharing and notes, the two fields the owner keeps on such a row (ADR-007).
    """
    command = requirements.RequirementUpdate(
        qty=qty, priority=priority, sharing=sharing, notes=notes
    )
    try:
        info = requirements.update_requirement(
            session_factory(request), principal, requirement_id, command
        )
    except USER_ERRORS as exc:
        status = 409 if isinstance(exc, CONFLICT_ERRORS) else 422
        return _row_error(request, principal, requirement_id, str(exc), status)
    return _row_or_redirect(request, principal, info)


@router.post("/requirements/{requirement_id}/consume")
def consume(
    request: Request,
    principal: Owner,
    requirement_id: str,
    qty: Annotated[int, Form()],
    note: Annotated[str, Form()] = "",
) -> Response:
    factory = session_factory(request)
    try:
        inventory.consume(factory, principal, requirement_id, qty, note=note)
    except USER_ERRORS as exc:
        return _row_error(request, principal, requirement_id, str(exc), 422)
    info = requirements.get_requirement(factory, principal, requirement_id)
    return _row_or_redirect(request, principal, info)
