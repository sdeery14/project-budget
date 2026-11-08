"""Projects: create, rename, archive. Archiving hides a project; nothing is deleted."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import validate_name
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import Project
from projectbudget.persistence.session import SessionFactory
from projectbudget.services.clock import utcnow
from projectbudget.services.guards import NotFoundError, require_write


class ProjectNotFoundError(NotFoundError):
    entity = "Project"


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: str
    name: str
    archived: bool
    notes: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectCreate:
    name: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ProjectUpdate:
    """Fields left as None keep their current value."""

    name: str | None = None
    notes: str | None = None
    archived: bool | None = None


def _info(row: Project) -> ProjectInfo:
    return ProjectInfo(
        id=row.id,
        name=row.name,
        archived=row.archived,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def load_project(db: Session, project_id: str) -> Project:
    """Fetch a project row inside a caller's transaction, or raise."""
    row = db.get(Project, project_id)
    if row is None:
        raise ProjectNotFoundError(project_id)
    return row


def create_project(
    session_factory: SessionFactory,
    principal: Principal,
    command: ProjectCreate,
    *,
    now: datetime | None = None,
) -> ProjectInfo:
    require_write(principal)
    name = validate_name(command.name)
    moment = now or utcnow()
    with session_factory.begin() as db:
        row = Project(
            id=str(uuid.uuid4()),
            name=name,
            archived=False,
            notes=command.notes.strip(),
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        return _info(row)


def update_project(
    session_factory: SessionFactory,
    principal: Principal,
    project_id: str,
    command: ProjectUpdate,
    *,
    now: datetime | None = None,
) -> ProjectInfo:
    require_write(principal)
    name = validate_name(command.name) if command.name is not None else None
    with session_factory.begin() as db:
        row = load_project(db, project_id)
        if name is not None:
            row.name = name
        if command.notes is not None:
            row.notes = command.notes.strip()
        if command.archived is not None:
            row.archived = command.archived
        row.updated_at = now or utcnow()
        return _info(row)


def get_project(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> ProjectInfo:
    with session_factory() as db:
        return _info(load_project(db, project_id))


def list_projects(
    session_factory: SessionFactory, principal: Principal, *, include_archived: bool = False
) -> list[ProjectInfo]:
    query = select(Project).order_by(Project.name, Project.id)
    if not include_archived:
        query = query.where(Project.archived.is_(False))
    with session_factory() as db:
        return [_info(row) for row in db.scalars(query).all()]
