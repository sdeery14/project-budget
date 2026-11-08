"""The purchase lifecycle: planned, ordered, partial receipt, received, cancelled; lines; header edits."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import InvalidQuantityError, MovementReason, PurchaseStatus
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, projects, purchases, requirements
from projectbudget.services.purchases import PurchaseInfo

NOW = datetime(2026, 11, 8, 9, 0, 0)


def usd(text: str) -> Money:
    return Money.parse(text, "USD")


@pytest.fixture
def clamp(session_factory: SessionFactory, principal: Principal) -> catalog.CatalogItemInfo:
    return catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))


@pytest.fixture
def glue(session_factory: SessionFactory, principal: Principal) -> catalog.CatalogItemInfo:
    return catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Glue"))


def _ordered(
    session_factory: SessionFactory, principal: Principal, *lines: tuple[str, int, str]
) -> PurchaseInfo:
    return purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=tuple(
                purchases.PurchaseLineCreate(item, qty, usd(price)) for item, qty, price in lines
            ),
            status=PurchaseStatus.ORDERED,
            ordered_on=date(2026, 11, 5),
        ),
        now=NOW,
    )


def test_partial_receipt_moves_stock_and_keeps_the_rest_on_order(
    session_factory: SessionFactory,
    principal: Principal,
    clamp: catalog.CatalogItemInfo,
    glue: catalog.CatalogItemInfo,
) -> None:
    order = _ordered(session_factory, principal, (clamp.id, 3, "12.50"), (glue.id, 2, "4.00"))
    assert order.status is PurchaseStatus.ORDERED
    assert order.remaining_qty == 5
    clamp_line = order.lines[0]

    partial = purchases.receive_line(
        session_factory, principal, clamp_line.id, 1, occurred_on=date(2026, 11, 7), now=NOW
    )
    assert partial.status is PurchaseStatus.ORDERED  # still open: 2 clamps and 2 glue to come
    assert partial.lines[0].received_qty == 1
    assert partial.lines[0].remaining == 2
    assert partial.remaining_qty == 4
    assert inventory.on_hand(session_factory, principal, clamp.id) == 1
    movement = inventory.list_movements(session_factory, principal, clamp.id)[0]
    assert (movement.reason, movement.delta_qty, movement.occurred_on) == (
        MovementReason.RECEIVED,
        1,
        date(2026, 11, 7),
    )
    assert movement.purchase_line_id == clamp_line.id

    with session_factory() as db:
        assert purchases.on_order_by_item(db) == {clamp.id: 2, glue.id: 2}

    done = purchases.receive_all(session_factory, principal, order.id, now=NOW)
    assert done.status is PurchaseStatus.RECEIVED
    assert done.remaining_qty == 0
    assert inventory.on_hand(session_factory, principal, clamp.id) == 3
    assert inventory.on_hand(session_factory, principal, glue.id) == 2
    with session_factory() as db:
        assert purchases.on_order_by_item(db) == {}


def test_receiving_more_than_the_line_or_on_a_closed_purchase_is_refused(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    order = _ordered(session_factory, principal, (clamp.id, 2, "1.00"))
    line = order.lines[0]
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.receive_line(session_factory, principal, line.id, 3)
    with pytest.raises(InvalidQuantityError):
        purchases.receive_line(session_factory, principal, line.id, 0)
    purchases.receive_all(session_factory, principal, order.id)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.receive_line(session_factory, principal, line.id, 1)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.receive_all(session_factory, principal, order.id)
    with pytest.raises(purchases.PurchaseLineNotFoundError):
        purchases.receive_line(session_factory, principal, "nope", 1)


def test_status_transitions(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    planned = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(clamp.id, 2, usd("1.00")),),
            status=PurchaseStatus.PLANNED,
        ),
    )
    with session_factory() as db:
        assert purchases.on_order_by_item(db) == {}  # planned counts nowhere
    ordered = purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.ORDERED)
    assert ordered.status is PurchaseStatus.ORDERED
    back = purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.PLANNED)
    assert back.status is PurchaseStatus.PLANNED
    # Receiving on a planned purchase makes it at least ordered.
    partly = purchases.receive_line(session_factory, principal, planned.lines[0].id, 1)
    assert partly.status is PurchaseStatus.ORDERED
    with pytest.raises(purchases.PurchaseStateError):
        purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.PLANNED)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.CANCELLED)
    received = purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.RECEIVED)
    assert received.status is PurchaseStatus.RECEIVED
    assert inventory.on_hand(session_factory, principal, clamp.id) == 2
    with pytest.raises(purchases.PurchaseStateError):
        purchases.set_status(session_factory, principal, planned.id, PurchaseStatus.CANCELLED)

    fresh = _ordered(session_factory, principal, (clamp.id, 1, "1.00"))
    cancelled = purchases.set_status(session_factory, principal, fresh.id, PurchaseStatus.CANCELLED)
    assert cancelled.status is PurchaseStatus.CANCELLED
    with session_factory() as db:
        assert purchases.total_since(db, date(2026, 1, 1), "USD") == usd(
            "2.00"
        )  # cancelled excluded
    reopened = purchases.set_status(session_factory, principal, fresh.id, PurchaseStatus.PLANNED)
    assert reopened.status is PurchaseStatus.PLANNED


def test_lines_can_be_added_and_removed_while_open(
    session_factory: SessionFactory,
    principal: Principal,
    clamp: catalog.CatalogItemInfo,
    glue: catalog.CatalogItemInfo,
) -> None:
    order = _ordered(session_factory, principal, (clamp.id, 1, "1.00"))
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.remove_line(session_factory, principal, order.id, order.lines[0].id)  # last line
    grown = purchases.add_line(
        session_factory, principal, order.id, purchases.PurchaseLineCreate(glue.id, 2, usd("4.00"))
    )
    assert [line.catalog_item_id for line in grown.lines] == [clamp.id, glue.id]
    assert grown.total == usd("9.00")
    purchases.receive_line(session_factory, principal, grown.lines[1].id, 1)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.remove_line(session_factory, principal, order.id, grown.lines[1].id)
    shrunk = purchases.remove_line(session_factory, principal, order.id, grown.lines[0].id)
    assert [line.catalog_item_id for line in shrunk.lines] == [glue.id]
    with pytest.raises(purchases.PurchaseLineNotFoundError):
        purchases.remove_line(session_factory, principal, order.id, "nope")
    other = _ordered(session_factory, principal, (clamp.id, 1, "1.00"))
    with pytest.raises(purchases.PurchaseLineNotFoundError):
        purchases.remove_line(session_factory, principal, order.id, other.lines[0].id)
    purchases.receive_all(session_factory, principal, order.id)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.add_line(
            session_factory,
            principal,
            order.id,
            purchases.PurchaseLineCreate(clamp.id, 1, usd("1.00")),
        )


def test_header_edits(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    order = _ordered(session_factory, principal, (clamp.id, 1, "10.00"))
    edited = purchases.update_purchase(
        session_factory,
        principal,
        order.id,
        purchases.PurchaseUpdate(
            vendor="  Tool  Shop ", ordered_on=date(2026, 11, 6), shipping=usd("5.00"), notes=" x "
        ),
    )
    assert (edited.vendor, edited.ordered_on, edited.notes) == ("Tool Shop", date(2026, 11, 6), "x")
    assert edited.total == usd("15.00")
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.update_purchase(
            session_factory, principal, order.id, purchases.PurchaseUpdate(tax=usd("-1.00"))
        )
    purchases.set_status(session_factory, principal, order.id, PurchaseStatus.CANCELLED)
    with pytest.raises(purchases.PurchaseStateError):
        purchases.update_purchase(
            session_factory, principal, order.id, purchases.PurchaseUpdate(vendor="later")
        )


def test_backlog_shows_partial_receipt_as_on_order_and_stock(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Bench"))
    requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=3, catalog_item_id=clamp.id),
    )
    order = _ordered(session_factory, principal, (clamp.id, 2, "12.50"))
    purchases.receive_line(session_factory, principal, order.lines[0].id, 1)
    (row,) = backlog.get_backlog(session_factory, principal).unpriced
    assert (row.needed, row.on_hand, row.gross_shortfall, row.on_order, row.net_shortfall) == (
        3,
        1,
        2,
        1,
        1,
    )
