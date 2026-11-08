"""Purchase lifecycle changes are serialised: two receipts can never both take the last unit."""

import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import PurchaseStatus
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, inventory, purchases
from projectbudget.services.purchases import PurchaseInfo

BARRIER_TIMEOUT = 1.5  # shorter than SQLite's 5 s busy timeout, so the lock waiter wins


@pytest.fixture
def order(session_factory: SessionFactory, principal: Principal) -> PurchaseInfo:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Vise"))
    return purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(item.id, 1, Money(8900, "USD")),),
            status=PurchaseStatus.ORDERED,
            ordered_on=date(2026, 11, 5),
        ),
    )


def _pause_before_writing(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Hold every worker right before the movement is written: the window the race needs."""
    barrier = threading.Barrier(parties)
    real = (
        inventory.record_receipt
    )  # purchases imports it from inventory; patch where it is looked up

    def paused(db: Session, **kwargs: Any) -> Any:
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=BARRIER_TIMEOUT)
        return real(db, **kwargs)

    monkeypatch.setattr(purchases, "record_receipt", paused)


def _outcome(fn: Callable[[], Any]) -> str:
    try:
        fn()
    except purchases.InvalidPurchaseError:
        return "refused"
    except purchases.PurchaseStateError:
        return "closed"
    return "ok"


def test_two_receipts_cannot_both_take_the_last_unit(
    session_factory: SessionFactory,
    principal: Principal,
    order: PurchaseInfo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pause_before_writing(monkeypatch, 2)
    line = order.lines[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(
            pool.map(
                lambda _: _outcome(
                    lambda: purchases.receive_line(session_factory, principal, line.id, 1)
                ),
                range(2),
            )
        )
    # The loser is refused either for quantity (re-read under the lock) or because the
    # purchase closed when the winner's receipt completed it. Never a second receipt.
    assert results.count("ok") == 1
    assert set(results) <= {"ok", "refused", "closed"}
    after = purchases.get_purchase(session_factory, principal, order.id)
    assert after.lines[0].received_qty == 1
    assert after.status is PurchaseStatus.RECEIVED
    assert inventory.on_hand(session_factory, principal, line.catalog_item_id) == 1


def test_receive_all_and_receive_line_are_serialised_together(
    session_factory: SessionFactory,
    principal: Principal,
    order: PurchaseInfo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pause_before_writing(monkeypatch, 2)
    line = order.lines[0]
    actions = (
        lambda: purchases.receive_all(session_factory, principal, order.id),
        lambda: purchases.receive_line(session_factory, principal, line.id, 1),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda i: _outcome(actions[i]), range(2)))
    assert results.count("ok") == 1
    assert set(results) <= {"ok", "refused", "closed"}
    assert inventory.on_hand(session_factory, principal, line.catalog_item_id) == 1


def test_many_receipts_stop_exactly_at_the_ordered_quantity(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Bolt"))
    order = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(item.id, 5, Money(45, "USD")),),
            status=PurchaseStatus.ORDERED,
        ),
    )
    line = order.lines[0]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda _: _outcome(
                    lambda: purchases.receive_line(session_factory, principal, line.id, 1)
                ),
                range(12),
            )
        )
    assert results.count("ok") == 5
    assert len(results) - results.count("ok") == 7
    assert inventory.on_hand(session_factory, principal, item.id) == 5
    assert purchases.get_purchase(session_factory, principal, order.id).lines[0].received_qty == 5
