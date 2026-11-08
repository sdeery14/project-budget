"""The consolidated backlog and its workspace-level order (ADR-004).

This module only gathers facts from the database and hands them to the pure
``domain.consolidation`` module. On-order is zero until purchases land (PR5).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import Intent, ItemKind, Sharing
from projectbudget.domain.consolidation import (
    Backlog,
    ItemFacts,
    RequirementLine,
    consolidate,
)
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import BacklogOrder, CatalogItem, PriceEstimate, Requirement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.catalog import load_item
from projectbudget.services.guards import require_write
from projectbudget.services.inventory import consumed_by_requirement, on_hand_by_item


class DuplicateOrderError(ValueError):
    """The same item appears twice in a backlog order."""


def get_backlog(session_factory: SessionFactory, principal: Principal) -> Backlog:
    currency = workspace.get_currency(session_factory)
    with session_factory() as db:
        items = db.scalars(select(CatalogItem)).all()
        requirements = db.scalars(
            select(Requirement).where(Requirement.intent == Intent.ACTIVE.value)
        ).all()
        consumed = consumed_by_requirement(db, (r.id for r in requirements))
        on_hand = on_hand_by_item(db)
        estimates = _current_estimates(db)
        order = db.scalars(
            select(BacklogOrder.catalog_item_id).order_by(BacklogOrder.position)
        ).all()

    facts = [
        ItemFacts(
            id=item.id,
            slug=item.slug,
            name=item.name,
            kind=ItemKind(item.kind),
            unit=item.unit,
            on_hand=on_hand.get(item.id, 0),
            on_order=0,
            unit_price=Money(estimates[item.id].amount, estimates[item.id].currency)
            if item.id in estimates
            else None,
            price_as_of=estimates[item.id].as_of if item.id in estimates else None,
        )
        for item in items
    ]
    lines = [
        RequirementLine(
            id=r.id,
            project_id=r.project_id,
            catalog_item_id=r.catalog_item_id,
            qty=r.qty,
            priority=r.priority,
            sharing=Sharing(r.sharing),
            intent=Intent(r.intent),
            consumed=consumed.get(r.id, 0),
        )
        for r in requirements
    ]
    return consolidate(facts, lines, order=list(order), currency=currency)


def _current_estimates(db: Session) -> dict[str, PriceEstimate]:
    """Latest ``as_of`` per item, ties to the newest record (same rule as catalog.current_estimate)."""
    rows = db.scalars(
        select(PriceEstimate).order_by(
            PriceEstimate.catalog_item_id,
            PriceEstimate.as_of.desc(),
            PriceEstimate.created_at.desc(),
            PriceEstimate.id.desc(),
        )
    ).all()
    current: dict[str, PriceEstimate] = {}
    for row in rows:
        current.setdefault(row.catalog_item_id, row)
    return current


def set_order(
    session_factory: SessionFactory,
    principal: Principal,
    item_ids: Sequence[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Replace the workspace backlog order. Every id must be a catalog item, each once."""
    require_write(principal)
    ids = list(item_ids)
    if len(set(ids)) != len(ids):
        raise DuplicateOrderError("An item may appear only once in the backlog order")
    with session_factory.begin() as db:
        for item_id in ids:
            load_item(db, item_id)
        db.execute(delete(BacklogOrder))
        db.add_all(
            BacklogOrder(catalog_item_id=item_id, position=index)
            for index, item_id in enumerate(ids)
        )
    return ids


def move(
    session_factory: SessionFactory, principal: Principal, item_id: str, *, up: bool
) -> list[str]:
    """Move one row a step up or down within the current display order and persist the whole order."""
    require_write(principal)
    current = list(get_backlog(session_factory, principal).order)
    if item_id not in current:
        with session_factory() as db:
            load_item(db, item_id)  # raises for unknown items; known ones simply are not listed
        return current
    index = current.index(item_id)
    target = index - 1 if up else index + 1
    if 0 <= target < len(current):
        current[index], current[target] = current[target], current[index]
    return set_order(session_factory, principal, current)
