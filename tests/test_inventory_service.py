"""Inventory movements: adjustments, consumption and the rules that guard them."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import Intent, ItemKind, MovementReason, Sharing
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, inventory, projects, requirements
from projectbudget.services.guards import ReadOnlyPrincipalError
from projectbudget.services.requirements import RequirementInfo

NOW = datetime(2026, 11, 8, 9, 0, 0)
DAY = date(2026, 11, 8)


@pytest.fixture
def project_id(session_factory: SessionFactory, principal: Principal) -> str:
    return projects.create_project(session_factory, principal, projects.ProjectCreate("Shed")).id


def _need(
    session_factory: SessionFactory,
    principal: Principal,
    project_id: str,
    name: str,
    qty: int,
    *,
    kind: ItemKind = ItemKind.CONSUMABLE,
    sharing: Sharing = Sharing.SHARED,
) -> RequirementInfo:
    item = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name=name, kind=kind)
    )
    return requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(
            project_id=project_id, qty=qty, catalog_item_id=item.id, sharing=sharing
        ),
    )


def test_adjustments_change_on_hand_and_are_listed_newest_first(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Bolt"))
    assert inventory.on_hand(session_factory, principal, item.id) == 0
    first = inventory.adjust(
        session_factory, principal, item.id, 5, occurred_on=DAY, note=" Stock take ", now=NOW
    )
    second = inventory.adjust(
        session_factory, principal, item.id, -2, occurred_on=DAY, note="Lost two", now=NOW
    )
    assert first.reason is MovementReason.ADJUSTMENT
    assert first.note == "Stock take"
    assert first.requirement_id is None
    assert inventory.on_hand(session_factory, principal, item.id) == 3
    listed = inventory.list_movements(session_factory, principal, item.id)
    assert [m.id for m in listed] == sorted([first.id, second.id])  # same instant: id order
    assert {m.delta_qty for m in listed} == {5, -2}


def test_adjustments_are_validated(session_factory: SessionFactory, principal: Principal) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Bolt"))
    with pytest.raises(inventory.InvalidMovementError):
        inventory.adjust(session_factory, principal, item.id, 0)
    with pytest.raises(inventory.InvalidMovementError):
        inventory.adjust(session_factory, principal, item.id, 1, note="x" * 256)
    with pytest.raises(inventory.InsufficientOnHandError):
        inventory.adjust(session_factory, principal, item.id, -1)
    with pytest.raises(catalog.CatalogItemNotFoundError):
        inventory.adjust(session_factory, principal, "nope", 1)
    assert inventory.list_movements(session_factory, principal, item.id) == []


def test_consumption_allocates_stock_to_a_requirement(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> None:
    req = _need(session_factory, principal, project_id, "Bolt", 10)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 6, now=NOW)
    used = inventory.consume(
        session_factory, principal, req.id, 4, occurred_on=DAY, note="Fitted", now=NOW
    )
    assert used.reason is MovementReason.CONSUMED
    assert used.delta_qty == -4
    assert used.requirement_id == req.id
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 2
    assert inventory.consumed(session_factory, principal, req.id) == 4


def test_consumption_against_a_shared_reusable_requirement_is_rejected(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> None:
    shared = _need(session_factory, principal, project_id, "Clamp", 3, kind=ItemKind.REUSABLE)
    inventory.adjust(session_factory, principal, shared.catalog_item_id, 3)
    with pytest.raises(inventory.SharedReusableConsumptionError):
        inventory.consume(session_factory, principal, shared.id, 1)
    # Exclusive reusable requirements may be consumed against: installing is the consumption.
    exclusive = requirements.update_requirement(
        session_factory,
        principal,
        shared.id,
        requirements.RequirementUpdate(sharing=Sharing.EXCLUSIVE),
    )
    assert inventory.consume(session_factory, principal, exclusive.id, 1).delta_qty == -1


def test_on_hand_never_goes_negative_through_consumption(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> None:
    req = _need(session_factory, principal, project_id, "Bolt", 10)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 2)
    with pytest.raises(inventory.InsufficientOnHandError):
        inventory.consume(session_factory, principal, req.id, 3)
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 2
    assert inventory.consumed(session_factory, principal, req.id) == 0


def test_consumption_never_exceeds_the_requirement(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> None:
    req = _need(session_factory, principal, project_id, "Bolt", 2)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 10)
    inventory.consume(session_factory, principal, req.id, 2)
    with pytest.raises(inventory.OverConsumptionError):
        inventory.consume(session_factory, principal, req.id, 1)


def test_only_active_requirements_can_be_consumed_against(
    session_factory: SessionFactory, principal: Principal, project_id: str
) -> None:
    req = _need(session_factory, principal, project_id, "Bolt", 2)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 10)
    requirements.set_intent(session_factory, principal, req.id, Intent.PAUSED)
    with pytest.raises(inventory.RequirementNotActiveError):
        inventory.consume(session_factory, principal, req.id, 1)
    with pytest.raises(requirements.RequirementNotFoundError):
        inventory.consume(session_factory, principal, "nope", 1)


def test_read_only_principals_cannot_move_stock(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    project_id: str,
) -> None:
    req = _need(session_factory, principal, project_id, "Bolt", 2)
    inventory.adjust(session_factory, principal, req.catalog_item_id, 1)
    with pytest.raises(ReadOnlyPrincipalError):
        inventory.adjust(session_factory, read_only_principal, req.catalog_item_id, 1)
    with pytest.raises(ReadOnlyPrincipalError):
        inventory.consume(session_factory, read_only_principal, req.id, 1)
    assert inventory.on_hand(session_factory, read_only_principal, req.catalog_item_id) == 1
