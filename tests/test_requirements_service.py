"""Requirements: item resolution, ordering, updates, intent changes, read-only guard."""

from datetime import datetime

import pytest

from projectbudget.domain.catalog import (
    Intent,
    InvalidPriorityError,
    InvalidQuantityError,
    ItemKind,
    ReviewState,
    Sharing,
)
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, projects, requirements
from projectbudget.services.guards import ReadOnlyPrincipalError
from projectbudget.services.projects import ProjectInfo

NOW = datetime(2026, 11, 8, 9, 0, 0)
LATER = datetime(2026, 11, 8, 10, 0, 0)


@pytest.fixture
def project(session_factory: SessionFactory, principal: Principal) -> ProjectInfo:
    return projects.create_project(session_factory, principal, projects.ProjectCreate(name="Bike"))


def test_free_text_creates_an_unreviewed_item_once(
    session_factory: SessionFactory, principal: Principal, project: ProjectInfo
) -> None:
    first = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(
            project_id=project.id, qty=2, item_text="Brake Pads", unit="pair"
        ),
        now=NOW,
    )
    item = catalog.get_item(session_factory, principal, first.catalog_item_id)
    assert item.slug == "brake-pads"
    assert item.name == "Brake Pads"
    assert item.unit == "pair"
    assert item.kind is ItemKind.CONSUMABLE
    assert item.review_state is ReviewState.UNREVIEWED

    other = projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="Trike")
    )
    second = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=other.id, qty=1, item_text="brake pads"),
    )
    assert second.catalog_item_id == item.id
    assert len(catalog.list_items(session_factory, principal)) == 1


def test_defaults_and_sort_order(
    session_factory: SessionFactory, principal: Principal, project: ProjectInfo
) -> None:
    clamp = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Clamp", kind=ItemKind.REUSABLE)
    )
    first = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=3, catalog_item_id=clamp.id),
        now=NOW,
    )
    second = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(
            project_id=project.id,
            qty=1,
            priority=1,
            item_text="Chain",
            sharing=Sharing.EXCLUSIVE,
            notes=" fit first ",
        ),
        now=NOW,
    )
    assert (first.priority, first.sharing, first.intent) == (2, Sharing.SHARED, Intent.ACTIVE)
    assert first.archive_reason is None
    assert first.notes == ""
    assert (first.sort_order, second.sort_order) == (0, 1)
    assert second.sharing is Sharing.EXCLUSIVE
    assert second.notes == "fit first"
    assert requirements.list_requirements(session_factory, principal, project.id) == [first, second]

    moved = requirements.update_requirement(
        session_factory,
        principal,
        second.id,
        requirements.RequirementUpdate(
            sort_order=-1, qty=2, priority=3, sharing=Sharing.SHARED, notes=""
        ),
        now=LATER,
    )
    assert (moved.sort_order, moved.qty, moved.priority, moved.sharing) == (
        -1,
        2,
        3,
        Sharing.SHARED,
    )
    assert moved.updated_at == LATER
    assert moved.created_at == NOW
    assert requirements.list_requirements(session_factory, principal, project.id) == [moved, first]


def test_item_reference_must_be_exactly_one_of_id_or_text(
    session_factory: SessionFactory, principal: Principal, project: ProjectInfo
) -> None:
    clamp = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    with pytest.raises(requirements.ItemReferenceError):
        requirements.add_requirement(
            session_factory, principal, requirements.RequirementCreate(project_id=project.id, qty=1)
        )
    with pytest.raises(requirements.ItemReferenceError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id, qty=1, catalog_item_id=clamp.id, item_text="Clamp"
            ),
        )
    with pytest.raises(catalog.CatalogItemNotFoundError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(project_id=project.id, qty=1, catalog_item_id="nope"),
        )
    with pytest.raises(projects.ProjectNotFoundError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(project_id="nope", qty=1, catalog_item_id=clamp.id),
        )
    # Nothing was half-written.
    assert requirements.list_requirements(session_factory, principal, project.id) == []
    assert len(catalog.list_items(session_factory, principal)) == 1


def test_quantity_and_priority_are_validated_before_any_write(
    session_factory: SessionFactory, principal: Principal, project: ProjectInfo
) -> None:
    with pytest.raises(InvalidQuantityError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(project_id=project.id, qty=0, item_text="Chain"),
        )
    with pytest.raises(InvalidPriorityError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id, qty=1, priority=4, item_text="Chain"
            ),
        )
    assert catalog.list_items(session_factory, principal) == []
    created = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Chain"),
    )
    with pytest.raises(InvalidQuantityError):
        requirements.update_requirement(
            session_factory, principal, created.id, requirements.RequirementUpdate(qty=-1)
        )
    with pytest.raises(InvalidPriorityError):
        requirements.update_requirement(
            session_factory, principal, created.id, requirements.RequirementUpdate(priority=0)
        )
    assert requirements.get_requirement(session_factory, principal, created.id) == created


def test_intent_changes_keep_the_row_and_only_archived_takes_a_reason(
    session_factory: SessionFactory, principal: Principal, project: ProjectInfo
) -> None:
    created = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Chain"),
    )
    paused = requirements.set_intent(session_factory, principal, created.id, Intent.PAUSED)
    assert paused.intent is Intent.PAUSED
    assert requirements.list_requirements(session_factory, principal, project.id) == []

    archived = requirements.set_intent(
        session_factory,
        principal,
        created.id,
        Intent.ARCHIVED,
        archive_reason=" missing from manifest ",
    )
    assert archived.intent is Intent.ARCHIVED
    assert archived.archive_reason == "missing from manifest"

    with pytest.raises(requirements.InvalidArchiveReasonError):
        requirements.set_intent(
            session_factory, principal, created.id, Intent.ACTIVE, archive_reason="why"
        )
    with pytest.raises(requirements.InvalidArchiveReasonError):
        requirements.set_intent(
            session_factory, principal, created.id, Intent.ARCHIVED, archive_reason="x" * 256
        )

    reactivated = requirements.set_intent(session_factory, principal, created.id, Intent.ACTIVE)
    assert reactivated.intent is Intent.ACTIVE
    assert reactivated.archive_reason is None
    assert requirements.list_requirements(session_factory, principal, project.id) == [reactivated]
    with pytest.raises(requirements.RequirementNotFoundError):
        requirements.set_intent(session_factory, principal, "nope", Intent.PAUSED)


def test_listing_an_unknown_project_fails_loudly(
    session_factory: SessionFactory, principal: Principal
) -> None:
    with pytest.raises(projects.ProjectNotFoundError):
        requirements.list_requirements(session_factory, principal, "nope")


def test_read_only_principals_cannot_write(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    project: ProjectInfo,
) -> None:
    created = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Chain"),
    )
    with pytest.raises(ReadOnlyPrincipalError):
        requirements.add_requirement(
            session_factory,
            read_only_principal,
            requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Tyre"),
        )
    with pytest.raises(ReadOnlyPrincipalError):
        requirements.update_requirement(
            session_factory, read_only_principal, created.id, requirements.RequirementUpdate(qty=2)
        )
    with pytest.raises(ReadOnlyPrincipalError):
        requirements.set_intent(session_factory, read_only_principal, created.id, Intent.PAUSED)
    assert requirements.list_requirements(session_factory, read_only_principal, project.id) == [
        created
    ]
