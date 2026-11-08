"""Inventory movements: on-hand is a sum of facts, consumption is the allocation (RFC v3 §4).

Rules enforced here, each a named test:

- consumption against a shared reusable requirement is rejected (ADR-004);
- on-hand never goes negative through consumption; an adjustment with a note
  corrects a count instead;
- a requirement is never consumed beyond its quantity.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import (
    Intent,
    ItemKind,
    MovementReason,
    Sharing,
    validate_quantity,
)
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import InventoryMovement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services.catalog import load_item, lock_item
from projectbudget.services.clock import today, utcnow
from projectbudget.services.guards import require_write
from projectbudget.services.requirements import load_requirement

NOTE_MAX_LENGTH = 255


class SharedReusableConsumptionError(ValueError):
    """Shared reusable items move between projects; nothing is consumed against them."""

    def __init__(self, requirement_id: str) -> None:
        super().__init__(
            f"Requirement {requirement_id!r} is a shared reusable item and cannot be "
            "consumed against; mark it exclusive if this project keeps the item"
        )
        self.requirement_id = requirement_id


class InsufficientOnHandError(ValueError):
    def __init__(self, item_id: str, on_hand: int, wanted: int) -> None:
        super().__init__(f"Only {on_hand} on hand for item {item_id!r}; {wanted} wanted")
        self.item_id = item_id
        self.on_hand = on_hand
        self.wanted = wanted


class OverConsumptionError(ValueError):
    def __init__(self, requirement_id: str, remaining: int, wanted: int) -> None:
        super().__init__(
            f"Requirement {requirement_id!r} has {remaining} left to consume; {wanted} wanted"
        )
        self.requirement_id = requirement_id
        self.remaining = remaining
        self.wanted = wanted


class RequirementNotActiveError(ValueError):
    def __init__(self, requirement_id: str, intent: str) -> None:
        super().__init__(f"Requirement {requirement_id!r} is {intent}, not active")
        self.requirement_id = requirement_id


class InvalidMovementError(ValueError):
    """Zero delta, or a note that is too long."""


@dataclass(frozen=True, slots=True)
class MovementInfo:
    id: str
    catalog_item_id: str
    delta_qty: int
    reason: MovementReason
    requirement_id: str | None
    purchase_line_id: str | None
    occurred_on: date
    note: str
    created_at: datetime


def _info(row: InventoryMovement) -> MovementInfo:
    return MovementInfo(
        id=row.id,
        catalog_item_id=row.catalog_item_id,
        delta_qty=row.delta_qty,
        reason=MovementReason(row.reason),
        requirement_id=row.requirement_id,
        purchase_line_id=row.purchase_line_id,
        occurred_on=row.occurred_on,
        note=row.note,
        created_at=row.created_at,
    )


def _clean_note(note: str) -> str:
    clean = note.strip()
    if len(clean) > NOTE_MAX_LENGTH:
        raise InvalidMovementError(f"Note must be at most {NOTE_MAX_LENGTH} characters")
    return clean


def on_hand_in(db: Session, item_id: str) -> int:
    """On-hand inside a caller's transaction: the sum of every movement."""
    total = db.scalar(
        select(func.coalesce(func.sum(InventoryMovement.delta_qty), 0)).where(
            InventoryMovement.catalog_item_id == item_id
        )
    )
    return int(total or 0)


def on_hand_by_item(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(InventoryMovement.catalog_item_id, func.sum(InventoryMovement.delta_qty)).group_by(
            InventoryMovement.catalog_item_id
        )
    ).all()
    return {item_id: int(total) for item_id, total in rows}


def consumed_by_requirement(db: Session, requirement_ids: Iterable[str]) -> dict[str, int]:
    """Units consumed against each requirement (absolute value of the consumed deltas)."""
    ids = list(requirement_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(InventoryMovement.requirement_id, func.sum(InventoryMovement.delta_qty))
        .where(
            InventoryMovement.reason == MovementReason.CONSUMED.value,
            InventoryMovement.requirement_id.in_(ids),
        )
        .group_by(InventoryMovement.requirement_id)
    ).all()
    return {req_id: -int(total) for req_id, total in rows if req_id is not None}


def _record(
    db: Session,
    *,
    item_id: str,
    delta: int,
    reason: str,
    requirement_id: str | None,
    occurred_on: date | None,
    note: str,
    now: datetime | None,
    purchase_line_id: str | None = None,
) -> InventoryMovement:
    row = InventoryMovement(
        id=str(uuid.uuid4()),
        catalog_item_id=item_id,
        delta_qty=delta,
        reason=reason,
        requirement_id=requirement_id,
        purchase_line_id=purchase_line_id,
        occurred_on=occurred_on or today(),
        note=note,
        created_at=now or utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def adjust(
    session_factory: SessionFactory,
    principal: Principal,
    item_id: str,
    delta: int,
    *,
    occurred_on: date | None = None,
    note: str = "",
    now: datetime | None = None,
) -> MovementInfo:
    """Correct the count by ``delta`` (a stock take, a loss, a find). On-hand stays >= 0."""
    require_write(principal)
    if type(delta) is not int or delta == 0:
        raise InvalidMovementError("An adjustment changes the count by a non-zero whole number")
    clean_note = _clean_note(note)
    with session_factory.begin() as db:
        lock_item(db, item_id)  # serialises against concurrent adjustments and consumption
        current = on_hand_in(db, item_id)
        if current + delta < 0:
            raise InsufficientOnHandError(item_id, current, -delta)
        row = _record(
            db,
            item_id=item_id,
            delta=delta,
            reason=MovementReason.ADJUSTMENT.value,
            requirement_id=None,
            occurred_on=occurred_on,
            note=clean_note,
            now=now,
        )
        return _info(row)


def consume(
    session_factory: SessionFactory,
    principal: Principal,
    requirement_id: str,
    qty: int,
    *,
    occurred_on: date | None = None,
    note: str = "",
    now: datetime | None = None,
) -> MovementInfo:
    """Use ``qty`` units from stock for a requirement: the project now owns them."""
    require_write(principal)
    wanted = validate_quantity(qty)
    clean_note = _clean_note(note)
    with session_factory.begin() as db:
        requirement = load_requirement(db, requirement_id)
        if requirement.intent != Intent.ACTIVE.value:
            raise RequirementNotActiveError(requirement_id, requirement.intent)
        item = lock_item(db, requirement.catalog_item_id)  # before any count is read
        if item.kind == ItemKind.REUSABLE.value and requirement.sharing == Sharing.SHARED.value:
            raise SharedReusableConsumptionError(requirement_id)
        db.refresh(requirement)  # re-read qty and intent under the lock
        if requirement.intent != Intent.ACTIVE.value:
            raise RequirementNotActiveError(requirement_id, requirement.intent)
        already = consumed_by_requirement(db, [requirement_id]).get(requirement_id, 0)
        remaining = requirement.qty - already
        if wanted > remaining:
            raise OverConsumptionError(requirement_id, remaining, wanted)
        current = on_hand_in(db, item.id)
        if wanted > current:
            raise InsufficientOnHandError(item.id, current, wanted)
        row = _record(
            db,
            item_id=item.id,
            delta=-wanted,
            reason=MovementReason.CONSUMED.value,
            requirement_id=requirement.id,
            occurred_on=occurred_on,
            note=clean_note,
            now=now,
        )
        return _info(row)


def record_receipt(
    db: Session,
    *,
    item_id: str,
    qty: int,
    purchase_line_id: str,
    occurred_on: date,
    now: datetime,
) -> MovementInfo:
    """Put a received purchase line into stock, inside the caller's (item-locked) transaction."""
    row = _record(
        db,
        item_id=item_id,
        delta=validate_quantity(qty),
        reason=MovementReason.RECEIVED.value,
        requirement_id=None,
        occurred_on=occurred_on,
        note="",
        now=now,
        purchase_line_id=purchase_line_id,
    )
    return _info(row)


def on_hand(session_factory: SessionFactory, principal: Principal, item_id: str) -> int:
    with session_factory() as db:
        load_item(db, item_id)
        return on_hand_in(db, item_id)


def consumed(session_factory: SessionFactory, principal: Principal, requirement_id: str) -> int:
    with session_factory() as db:
        load_requirement(db, requirement_id)
        return consumed_by_requirement(db, [requirement_id]).get(requirement_id, 0)


def list_movements(
    session_factory: SessionFactory, principal: Principal, item_id: str
) -> list[MovementInfo]:
    """Newest first."""
    with session_factory() as db:
        load_item(db, item_id)
        rows = db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.catalog_item_id == item_id)
            .order_by(
                InventoryMovement.occurred_on.desc(),
                InventoryMovement.created_at.desc(),
                InventoryMovement.id,
            )
        ).all()
        return [_info(row) for row in rows]
