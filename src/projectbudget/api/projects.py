"""``/api/v1/projects``: the JSON twin of the projects pages."""

from fastapi import APIRouter, Depends, Request

from projectbudget.api.schemas import ProjectIn, ProjectOut, ProjectPatch, RequirementOut
from projectbudget.services import projects, requirements
from projectbudget.web.auth import Owner, require_owner, session_factory

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_owner)])


@router.get("")
def list_projects(
    request: Request, principal: Owner, include_archived: bool = False
) -> list[ProjectOut]:
    rows = projects.list_projects(
        session_factory(request), principal, include_archived=include_archived
    )
    return [ProjectOut.of(row) for row in rows]


@router.post("", status_code=201)
def create_project(request: Request, principal: Owner, body: ProjectIn) -> ProjectOut:
    info = projects.create_project(
        session_factory(request),
        principal,
        projects.ProjectCreate(name=body.name, notes=body.notes),
    )
    return ProjectOut.of(info)


@router.get("/{project_id}")
def get_project(request: Request, principal: Owner, project_id: str) -> ProjectOut:
    return ProjectOut.of(projects.get_project(session_factory(request), principal, project_id))


@router.patch("/{project_id}")
def update_project(
    request: Request, principal: Owner, project_id: str, body: ProjectPatch
) -> ProjectOut:
    info = projects.update_project(
        session_factory(request),
        principal,
        project_id,
        projects.ProjectUpdate(name=body.name, notes=body.notes, archived=body.archived),
    )
    return ProjectOut.of(info)


@router.get("/{project_id}/requirements")
def list_requirements(
    request: Request, principal: Owner, project_id: str, include_inactive: bool = False
) -> list[RequirementOut]:
    rows = requirements.list_requirements(
        session_factory(request), principal, project_id, include_inactive=include_inactive
    )
    return [RequirementOut.of(row) for row in rows]
