"""Stock checks are serialised: two callers can never both spend the last unit.

The deterministic tests hold every worker at a barrier right after it has read
the count, which is exactly the window the race needs. With the item row lock
only one worker can reach that point at a time, so the barrier times out for
the others and they re-read after the first has committed. The stress tests
simply hammer the same item from many threads and check the arithmetic.
"""

import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import ItemKind, Sharing
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, inventory, projects, requirements
from projectbudget.services.requirements import RequirementInfo

BARRIER_TIMEOUT = 1.5  # shorter than SQLite's 5 s busy timeout, so the lock waiter wins


def _need(
    session_factory: SessionFactory, principal: Principal, qty: int, *, on_hand: int
) -> RequirementInfo:
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    item = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="Vise", kind=ItemKind.REUSABLE),
    )
    if on_hand:
        inventory.adjust(session_factory, principal, item.id, on_hand)
    return requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(
            project_id=project.id, qty=qty, catalog_item_id=item.id, sharing=Sharing.EXCLUSIVE
        ),
    )


def _pause_after_reading(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Make every worker wait after it has read on-hand, until all are there or the wait times out."""
    barrier = threading.Barrier(parties)
    real = inventory.on_hand_in

    def paused(db: Session, item_id: str) -> int:
        value = real(db, item_id)
        # A timeout means the others are blocked behind the row lock; that is the point.
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=BARRIER_TIMEOUT)
        return value

    monkeypatch.setattr(inventory, "on_hand_in", paused)


def _run(workers: int, fn: Callable[[int], Any]) -> list[Any]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, range(workers)))


def _outcome(fn: Callable[[], Any]) -> str:
    try:
        fn()
    except inventory.InsufficientOnHandError:
        return "insufficient"
    except inventory.OverConsumptionError:
        return "over"
    return "ok"


def test_two_consumers_cannot_both_take_the_last_unit(
    session_factory: SessionFactory, principal: Principal, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = _need(session_factory, principal, 5, on_hand=1)
    _pause_after_reading(monkeypatch, 2)
    results = _run(
        2, lambda _: _outcome(lambda: inventory.consume(session_factory, principal, req.id, 1))
    )
    assert sorted(results) == ["insufficient", "ok"]
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 0
    assert inventory.consumed(session_factory, principal, req.id) == 1


def test_two_consumers_cannot_exceed_the_requirement_together(
    session_factory: SessionFactory, principal: Principal, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = _need(session_factory, principal, 1, on_hand=5)
    _pause_after_reading(monkeypatch, 2)
    results = _run(
        2, lambda _: _outcome(lambda: inventory.consume(session_factory, principal, req.id, 1))
    )
    assert sorted(results) == ["ok", "over"]
    assert inventory.consumed(session_factory, principal, req.id) == 1
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 4


def test_adjustment_and_consumption_are_serialised_together(
    session_factory: SessionFactory, principal: Principal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stock-take that removes the last unit and a consumption of it cannot both succeed."""
    req = _need(session_factory, principal, 5, on_hand=1)
    _pause_after_reading(monkeypatch, 2)
    actions = (
        lambda: inventory.adjust(session_factory, principal, req.catalog_item_id, -1, note="lost"),
        lambda: inventory.consume(session_factory, principal, req.id, 1),
    )
    results = _run(2, lambda i: _outcome(actions[i]))
    assert sorted(results) == ["insufficient", "ok"]
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 0


def test_many_consumers_stop_exactly_at_zero(
    session_factory: SessionFactory, principal: Principal
) -> None:
    req = _need(session_factory, principal, 50, on_hand=10)
    results = _run(
        16, lambda _: _outcome(lambda: inventory.consume(session_factory, principal, req.id, 1))
    )
    assert results.count("ok") == 10
    assert results.count("insufficient") == 6
    assert inventory.on_hand(session_factory, principal, req.catalog_item_id) == 0
    assert inventory.consumed(session_factory, principal, req.id) == 10


def test_many_adjustments_never_go_negative(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Bolt"))
    inventory.adjust(session_factory, principal, item.id, 5)
    results = _run(
        12, lambda _: _outcome(lambda: inventory.adjust(session_factory, principal, item.id, -1))
    )
    assert results.count("ok") == 5
    assert results.count("insufficient") == 7
    assert inventory.on_hand(session_factory, principal, item.id) == 0
