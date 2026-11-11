"""The backlog service wires facts from the database into the pure consolidation."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import Intent, ItemKind
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, projects, requirements
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 1, 9, 0, 0)
LATER = datetime(2026, 11, 1, 9, 5, 0)


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=date(2026, 11, 1))


def test_backlog_reflects_requirements_stock_and_prices(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    result = backlog.get_backlog(session_factory, principal)
    rows = {row.item.id: row for row in result.rows}
    assert rows[canonical.clamp.id].gross_shortfall == 2
    assert rows[canonical.clamp.id].shared is True
    assert rows[canonical.bolts.id].gross_shortfall == 11
    assert rows[canonical.vise.id].gross_shortfall == 1
    assert rows[canonical.vise.id].shared is False
    assert [row.item.id for row in result.unpriced] == [canonical.hinge_set.id]
    assert result.total == Money(2500, "USD") + Money(495, "USD") + Money(8900, "USD")
    assert result.is_lower_bound
    # No workspace order yet: priority first (P1 hinges are unpriced), then name.
    assert [row.item.name for row in result.rows] == [
        "Bar clamp 600 mm",
        "Bench vise 125 mm",
        "M8 carriage bolt 80 mm",
    ]


def test_consumption_and_intent_changes_move_the_numbers(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    inventory.adjust(session_factory, principal, canonical.vise.id, 1)
    inventory.consume(session_factory, principal, canonical.workbench_vise.id, 1)
    requirements.set_intent(session_factory, principal, canonical.gate_bolts.id, Intent.CANCELLED)
    result = backlog.get_backlog(session_factory, principal)
    rows = {row.item.id: row for row in result.rows}
    assert canonical.vise.id not in rows  # installed: exclusive need met by consumption
    assert rows[canonical.bolts.id].gross_shortfall == 6  # 10 - 4 on hand
    assert rows[canonical.bolts.id].project_ids == (canonical.workbench.id,)


def test_latest_estimate_is_used(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    catalog.add_estimate(
        session_factory,
        principal,
        canonical.clamp.id,
        catalog.EstimateCreate(price=Money(1000, "USD"), as_of=date(2026, 11, 5)),
    )
    result = backlog.get_backlog(session_factory, principal)
    row = next(r for r in result.rows if r.item.id == canonical.clamp.id)
    assert row.unit_price == Money(1000, "USD")
    assert row.item.price_as_of == date(2026, 11, 5)
    assert row.line_total == Money(2000, "USD")


def test_default_order_follows_project_then_requirement_order(
    session_factory: SessionFactory, principal: Principal
) -> None:
    """ADR-012 (#34): projects rank in creation order; within one, by sort_order, never by name."""
    garage = projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="Garage"), now=NOW
    )
    home = projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="Home"), now=LATER
    )

    def priced(name: str) -> str:
        info = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name=name))
        catalog.add_estimate(
            session_factory, principal, info.id, catalog.EstimateCreate(price=Money(100, "USD"))
        )
        return info.id

    def need(project_id: str, item_id: str, *, priority: int = 2) -> None:
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project_id, qty=1, priority=priority, catalog_item_id=item_id
            ),
        )

    need(home.id, priced("Raspberry Pi"), priority=1)
    need(home.id, priced("Active cooler"), priority=1)
    need(garage.id, priced("Tyre sealant"))
    need(garage.id, priced("Shock pump"), priority=1)

    names = [row.item.name for row in backlog.get_backlog(session_factory, principal).rows]
    assert names == ["Shock pump", "Raspberry Pi", "Active cooler", "Tyre sealant"]


def test_set_order_and_move(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    ids = [canonical.bolts.id, canonical.clamp.id]
    assert backlog.set_order(session_factory, principal, ids) == ids
    names = [row.item.name for row in backlog.get_backlog(session_factory, principal).rows]
    assert names == ["M8 carriage bolt 80 mm", "Bar clamp 600 mm", "Bench vise 125 mm"]

    backlog.move(session_factory, principal, canonical.vise.id, up=True)
    names = [row.item.name for row in backlog.get_backlog(session_factory, principal).rows]
    assert names == ["M8 carriage bolt 80 mm", "Bench vise 125 mm", "Bar clamp 600 mm"]

    # Moving the first row up or the last row down is a no-op, not an error.
    order = backlog.move(session_factory, principal, canonical.bolts.id, up=True)
    assert order[0] == canonical.bolts.id
    unpriced_last = backlog.move(session_factory, principal, canonical.hinge_set.id, up=False)
    assert unpriced_last[-1] == canonical.hinge_set.id


def test_order_validation(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    with pytest.raises(backlog.DuplicateOrderError):
        backlog.set_order(session_factory, principal, [canonical.clamp.id, canonical.clamp.id])
    with pytest.raises(catalog.CatalogItemNotFoundError):
        backlog.set_order(session_factory, principal, ["nope"])
    with pytest.raises(catalog.CatalogItemNotFoundError):
        backlog.move(session_factory, principal, "nope", up=True)
    # An item that is not on the backlog (no shortfall) cannot be moved but is not an error.
    covered = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="Spare", kind=ItemKind.CONSUMABLE),
    )
    assert covered.id not in backlog.move(session_factory, principal, covered.id, up=True)


def test_read_only_principals_can_read_but_not_reorder(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    canonical: CanonicalWorkspace,
) -> None:
    assert backlog.get_backlog(session_factory, read_only_principal).rows
    with pytest.raises(ReadOnlyPrincipalError):
        backlog.set_order(session_factory, read_only_principal, [canonical.clamp.id])
    with pytest.raises(ReadOnlyPrincipalError):
        backlog.move(session_factory, read_only_principal, canonical.clamp.id, up=True)


def test_empty_workspace_has_an_empty_backlog(
    session_factory: SessionFactory, principal: Principal
) -> None:
    result = backlog.get_backlog(session_factory, principal)
    assert result.rows == ()
    assert result.unpriced == ()
    assert result.total == Money.zero("USD")
    assert not result.is_lower_bound
