"""Projects: create, update, archive; read-only principals may only read."""

from datetime import datetime

import pytest

from projectbudget.domain.catalog import InvalidNameError
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import projects
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 8, 9, 0, 0)
LATER = datetime(2026, 11, 8, 10, 0, 0)


def test_create_get_list_and_update(session_factory: SessionFactory, principal: Principal) -> None:
    bike = projects.create_project(
        session_factory,
        principal,
        projects.ProjectCreate(name="  Bike  ", notes=" cargo "),
        now=NOW,
    )
    assert bike.name == "Bike"
    assert bike.notes == "cargo"
    assert bike.archived is False
    assert bike.created_at == bike.updated_at == NOW
    assert projects.get_project(session_factory, principal, bike.id) == bike

    shed = projects.create_project(session_factory, principal, projects.ProjectCreate(name="Shed"))
    assert projects.list_projects(session_factory, principal) == [bike, shed]

    renamed = projects.update_project(
        session_factory,
        principal,
        bike.id,
        projects.ProjectUpdate(name="Cargo bike", notes=""),
        now=LATER,
    )
    assert renamed.name == "Cargo bike"
    assert renamed.notes == ""
    assert renamed.created_at == NOW
    assert renamed.updated_at == LATER


def test_archived_projects_are_hidden_by_default(
    session_factory: SessionFactory, principal: Principal
) -> None:
    bike = projects.create_project(session_factory, principal, projects.ProjectCreate(name="Bike"))
    shed = projects.create_project(session_factory, principal, projects.ProjectCreate(name="Shed"))
    archived = projects.update_project(
        session_factory, principal, bike.id, projects.ProjectUpdate(archived=True)
    )
    assert archived.archived is True
    assert projects.list_projects(session_factory, principal) == [shed]
    assert projects.list_projects(session_factory, principal, include_archived=True) == [
        archived,
        shed,
    ]
    restored = projects.update_project(
        session_factory, principal, bike.id, projects.ProjectUpdate(archived=False)
    )
    assert restored.archived is False


def test_validation_and_missing_projects(
    session_factory: SessionFactory, principal: Principal
) -> None:
    with pytest.raises(InvalidNameError):
        projects.create_project(session_factory, principal, projects.ProjectCreate(name="   "))
    with pytest.raises(projects.ProjectNotFoundError):
        projects.get_project(session_factory, principal, "nope")
    with pytest.raises(projects.ProjectNotFoundError):
        projects.update_project(
            session_factory, principal, "nope", projects.ProjectUpdate(name="x")
        )
    assert projects.list_projects(session_factory, principal) == []


def test_read_only_principals_cannot_write(
    session_factory: SessionFactory, principal: Principal, read_only_principal: Principal
) -> None:
    bike = projects.create_project(session_factory, principal, projects.ProjectCreate(name="Bike"))
    with pytest.raises(ReadOnlyPrincipalError):
        projects.create_project(
            session_factory, read_only_principal, projects.ProjectCreate(name="Shed")
        )
    with pytest.raises(ReadOnlyPrincipalError):
        projects.update_project(
            session_factory, read_only_principal, bike.id, projects.ProjectUpdate(archived=True)
        )
    assert projects.list_projects(session_factory, read_only_principal) == [bike]
