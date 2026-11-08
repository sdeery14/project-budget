"""The RFC v3 §4 invariants that PR4a can already prove, each as a named test on both backends.

Still to come with their PRs: priority of a backlog row and the shortfall rules
(PR4b), consumption and on-hand (PR6), catalog merge (PR6), sync no-op and
archive-on-missing (sync PRs). Foreign-key enforcement lives in test_engine.py.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from projectbudget.domain.catalog import Intent, ItemKind
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import Requirement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, projects, requirements, workspace

NOW = datetime(2026, 11, 8, 9, 0, 0)


def test_money_is_an_integer_in_minor_units_with_a_currency_code(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    saved = catalog.add_estimate(
        session_factory, principal, item.id, catalog.EstimateCreate(price=Money(1299, "USD"))
    )
    assert saved.price == Money(1299, "USD")
    assert isinstance(saved.price.amount, int)
    assert str(saved.price) == "12.99"


def test_mixed_currencies_are_rejected_at_write(
    session_factory: SessionFactory, principal: Principal
) -> None:
    assert workspace.get_currency(session_factory) == "USD"
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    with pytest.raises(CurrencyMismatchError):
        catalog.add_estimate(
            session_factory, principal, item.id, catalog.EstimateCreate(price=Money(1299, "EUR"))
        )
    assert catalog.list_estimates(session_factory, principal, item.id) == []


def test_a_requirement_always_references_a_catalog_item(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Bike"))
    # Free text is resolved to a catalog item inside the same transaction, never stored as text.
    created = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=2, item_text="Brake pads"),
    )
    item = catalog.get_item(session_factory, principal, created.catalog_item_id)
    assert item.slug == "brake-pads"
    # The database agrees: an orphan reference is refused on SQLite and PostgreSQL alike.
    with pytest.raises(IntegrityError), session_factory.begin() as db:
        db.add(
            Requirement(
                id=str(uuid.uuid4()),
                project_id=project.id,
                catalog_item_id=str(uuid.uuid4()),
                qty=1,
                priority=2,
                sort_order=0,
                intent="active",
                archive_reason=None,
                sharing="shared",
                notes="",
                created_at=NOW,
                updated_at=NOW,
            )
        )


def test_requirement_units_match_the_catalog_item(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    wire = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Wire", unit="m")
    )
    with pytest.raises(requirements.UnitMismatchError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id, qty=10, catalog_item_id=wire.id, unit="ft"
            ),
        )
    with pytest.raises(requirements.UnitMismatchError):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id, qty=10, item_text="Wire", unit="ft"
            ),
        )
    ok = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=10, item_text="wire", unit="M"),
    )
    assert ok.catalog_item_id == wire.id
    assert requirements.list_requirements(session_factory, principal, project.id) == [ok]


def test_cancelled_requirements_keep_their_history(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Desk"))
    created = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=4, item_text="Legs"),
    )
    cancelled = requirements.set_intent(session_factory, principal, created.id, Intent.CANCELLED)
    assert cancelled.intent is Intent.CANCELLED
    assert cancelled.qty == 4
    # Gone from the active view, still on record.
    assert requirements.list_requirements(session_factory, principal, project.id) == []
    assert requirements.list_requirements(
        session_factory, principal, project.id, include_inactive=True
    ) == [cancelled]
    assert requirements.get_requirement(session_factory, principal, created.id) == cancelled


def test_unpriced_items_never_contribute_zero(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Mystery part")
    )
    assert catalog.current_estimate(session_factory, principal, item.id) is None
    assert item.kind is ItemKind.CONSUMABLE
