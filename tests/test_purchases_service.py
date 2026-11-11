"""Purchases: recording, receipt into stock, spend since a date, on-order, validation."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import MovementReason, PurchaseStatus
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, projects, purchases, requirements
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 8, 9, 0, 0)


def usd(text: str) -> Money:
    return Money.parse(text, "USD")


@pytest.fixture
def clamp(session_factory: SessionFactory, principal: Principal) -> catalog.CatalogItemInfo:
    return catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))


def _line(item_id: str, qty: int, price: str) -> purchases.PurchaseLineCreate:
    return purchases.PurchaseLineCreate(catalog_item_id=item_id, qty=qty, unit_price=usd(price))


def test_received_purchase_puts_every_line_into_stock(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    glue = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Glue"))
    info = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(_line(clamp.id, 2, "12.50"), _line(glue.id, 1, "4.00")),
            vendor="  Tool  Shop ",
            ordered_on=date(2026, 11, 5),
            shipping=usd("6.00"),
            tax=usd("1.50"),
            notes=" first order ",
        ),
        now=NOW,
    )
    assert info.vendor == "Tool Shop"
    assert info.status is PurchaseStatus.RECEIVED
    assert info.total == usd("36.50")  # 25 + 4 + 6 + 1.50
    assert [line.received_qty for line in info.lines] == [2, 1]
    assert info.notes == "first order"
    assert inventory.on_hand(session_factory, principal, clamp.id) == 2
    assert inventory.on_hand(session_factory, principal, glue.id) == 1
    movements = inventory.list_movements(session_factory, principal, clamp.id)
    assert [(m.reason, m.delta_qty, m.occurred_on) for m in movements] == [
        (MovementReason.RECEIVED, 2, date(2026, 11, 5))
    ]
    assert movements[0].purchase_line_id == info.lines[0].id
    assert purchases.get_purchase(session_factory, principal, info.id) == info
    assert purchases.list_purchases(session_factory, principal) == [info]


def test_ordered_purchase_is_on_order_not_stock(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Bench"))
    requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=3, catalog_item_id=clamp.id),
    )
    catalog.add_estimate(
        session_factory, principal, clamp.id, catalog.EstimateCreate(price=usd("10.00"))
    )
    info = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(_line(clamp.id, 2, "10.00"),), status=PurchaseStatus.ORDERED
        ),
    )
    assert info.lines[0].received_qty == 0
    assert inventory.on_hand(session_factory, principal, clamp.id) == 0
    (row,) = backlog.get_backlog(session_factory, principal).rows
    assert (row.gross_shortfall, row.on_order, row.net_shortfall) == (3, 2, 1)
    assert row.line_total == usd("10.00")  # net of the two on order (ADR-012)
    assert row.on_order_cost == usd("20.00")


def test_total_since_counts_ordered_and_received_dated_on_or_after(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    def record(day: date, status: PurchaseStatus, price: str) -> None:
        purchases.record_purchase(
            session_factory,
            principal,
            purchases.PurchaseCreate(
                lines=(_line(clamp.id, 1, price),),
                ordered_on=day,
                status=status,
                shipping=usd("1.00"),
            ),
        )

    record(date(2026, 10, 31), PurchaseStatus.RECEIVED, "100.00")  # before: not counted
    record(date(2026, 11, 1), PurchaseStatus.RECEIVED, "10.00")  # on the day: counted (11)
    record(date(2026, 11, 5), PurchaseStatus.ORDERED, "20.00")  # ordered counts (21)
    record(date(2026, 11, 6), PurchaseStatus.PLANNED, "40.00")  # planned does not
    record(date(2026, 11, 7), PurchaseStatus.CANCELLED, "80.00")  # cancelled does not
    with session_factory() as db:
        assert purchases.total_since(db, date(2026, 11, 1), "USD") == usd("32.00")
        assert purchases.total_since(db, date(2027, 1, 1), "USD") == usd("0.00")


def test_mark_purchased_is_a_single_received_line(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    info = purchases.mark_purchased(
        session_factory, principal, clamp.id, 2, usd("12.50"), ordered_on=date(2026, 11, 8)
    )
    assert info.status is PurchaseStatus.RECEIVED
    assert info.total == usd("25.00")
    assert inventory.on_hand(session_factory, principal, clamp.id) == 2


def test_validation(
    session_factory: SessionFactory, principal: Principal, clamp: catalog.CatalogItemInfo
) -> None:
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.record_purchase(session_factory, principal, purchases.PurchaseCreate(lines=()))
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.record_purchase(
            session_factory,
            principal,
            purchases.PurchaseCreate(lines=(_line(clamp.id, 1, "1.00"),), shipping=usd("-1.00")),
        )
    with pytest.raises(purchases.InvalidPurchaseError):
        purchases.record_purchase(
            session_factory,
            principal,
            purchases.PurchaseCreate(lines=(_line(clamp.id, 1, "1.00"),), vendor="x" * 201),
        )
    with pytest.raises(CurrencyMismatchError):
        purchases.record_purchase(
            session_factory,
            principal,
            purchases.PurchaseCreate(
                lines=(purchases.PurchaseLineCreate(clamp.id, 1, Money(100, "EUR")),)
            ),
        )
    with pytest.raises(catalog.CatalogItemNotFoundError):
        purchases.record_purchase(
            session_factory, principal, purchases.PurchaseCreate(lines=(_line("nope", 1, "1.00"),))
        )
    with pytest.raises(purchases.PurchaseNotFoundError):
        purchases.get_purchase(session_factory, principal, "nope")
    assert purchases.list_purchases(session_factory, principal) == []
    assert inventory.on_hand(session_factory, principal, clamp.id) == 0


def test_read_only_principals_cannot_record_purchases(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    clamp: catalog.CatalogItemInfo,
) -> None:
    with pytest.raises(ReadOnlyPrincipalError):
        purchases.mark_purchased(session_factory, read_only_principal, clamp.id, 1, usd("1.00"))
    assert purchases.list_purchases(session_factory, read_only_principal) == []
