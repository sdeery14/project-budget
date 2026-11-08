"""The RFC v3 §4 invariants that PR4a can already prove, each as a named test on both backends.

Still to come with their PRs: "a purchase never implies project membership"
(PR5, purchases), catalog merge (PR6), sync no-op and archive-on-missing (sync
PRs). Foreign-key enforcement lives in test_engine.py.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from projectbudget.domain.catalog import Intent, ItemKind, MovementReason
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import Requirement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, projects, requirements, workspace

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


def test_consumption_against_a_shared_reusable_requirement_is_rejected(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Bench"))
    clamp = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Clamp", kind=ItemKind.REUSABLE)
    )
    shared = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=2, catalog_item_id=clamp.id),
    )
    inventory.adjust(session_factory, principal, clamp.id, 2)
    with pytest.raises(inventory.SharedReusableConsumptionError):
        inventory.consume(session_factory, principal, shared.id, 1)
    assert inventory.on_hand(session_factory, principal, clamp.id) == 2


def test_row_priority_equals_the_most_urgent_contributing_requirement(
    session_factory: SessionFactory, principal: Principal
) -> None:
    a = projects.create_project(session_factory, principal, projects.ProjectCreate("A"))
    b = projects.create_project(session_factory, principal, projects.ProjectCreate("B"))
    bolt = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Bolt"))
    for project, priority in ((a, 3), (b, 1)):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id, qty=1, priority=priority, catalog_item_id=bolt.id
            ),
        )
    (row,) = backlog.get_backlog(session_factory, principal).unpriced
    assert row.priority == 1
    assert set(row.project_ids) == {a.id, b.id}


def test_cancelled_requirements_leave_the_shortfall_but_keep_consumption_records(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    req = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=5, item_text="Screws"),
    )
    inventory.adjust(session_factory, principal, req.catalog_item_id, 2)
    inventory.consume(session_factory, principal, req.id, 2)
    assert backlog.get_backlog(session_factory, principal).unpriced[0].gross_shortfall == 3
    requirements.set_intent(session_factory, principal, req.id, Intent.CANCELLED)
    assert backlog.get_backlog(session_factory, principal).unpriced == ()
    assert inventory.consumed(session_factory, principal, req.id) == 2
    movements = inventory.list_movements(session_factory, principal, req.catalog_item_id)
    assert [m.requirement_id for m in movements if m.reason is MovementReason.CONSUMED] == [req.id]


def test_unpriced_totals_carry_an_unpriced_count(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    priced = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=2, item_text="Hinge"),
    )
    catalog.add_estimate(
        session_factory,
        principal,
        priced.catalog_item_id,
        catalog.EstimateCreate(price=Money(300, "USD")),
    )
    requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Latch"),
    )
    result = backlog.get_backlog(session_factory, principal)
    assert result.total == Money(600, "USD")
    assert result.unpriced_count == 1
    assert result.is_lower_bound


def test_on_hand_never_goes_negative_through_consumption_but_adjustments_correct_it(
    session_factory: SessionFactory, principal: Principal
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    req = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=5, item_text="Screws"),
    )
    with pytest.raises(inventory.InsufficientOnHandError):
        inventory.consume(session_factory, principal, req.id, 1)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 3, note="Found a box")
    inventory.consume(session_factory, principal, req.id, 3)
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 0
    with pytest.raises(inventory.InsufficientOnHandError):
        inventory.adjust(session_factory, principal, req.catalog_item_id, -1, note="Lost one")
