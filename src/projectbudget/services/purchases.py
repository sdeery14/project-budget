"""Purchases: facts about money spent or committed (RFC v3 §4). Never touched by sync.

Lifecycle: ``planned`` → ``ordered`` → ``received``, or ``cancelled`` while nothing has
arrived. Receiving a line (fully or in part) creates a ``received`` inventory
movement linked to that line; the unreceived remainder of an ordered purchase is
what the backlog shows as on-order. Refunds are deferred (RFC v3 §2), so a
received purchase cannot be cancelled.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import PurchaseStatus, validate_quantity
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import Purchase, PurchaseLine
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.catalog import load_live_item, lock_item
from projectbudget.services.clock import today, utcnow
from projectbudget.services.guards import NotFoundError, require_write
from projectbudget.services.inventory import record_receipt

VENDOR_MAX_LENGTH = 200
COUNTS_AS_SPENT = (PurchaseStatus.ORDERED.value, PurchaseStatus.RECEIVED.value)
OPEN_STATUSES = (PurchaseStatus.PLANNED, PurchaseStatus.ORDERED)


class PurchaseNotFoundError(NotFoundError):
    entity = "Purchase"


class PurchaseLineNotFoundError(NotFoundError):
    entity = "Purchase line"


class InvalidPurchaseError(ValueError):
    """No lines, a negative amount, a vendor that is too long, or more received than ordered."""


class PurchaseStateError(ValueError):
    """The purchase's status does not allow that change."""


@dataclass(frozen=True, slots=True)
class PurchaseLineCreate:
    catalog_item_id: str
    qty: int
    unit_price: Money


@dataclass(frozen=True, slots=True)
class PurchaseCreate:
    lines: tuple[PurchaseLineCreate, ...]
    vendor: str = ""
    ordered_on: date | None = None
    status: PurchaseStatus = PurchaseStatus.RECEIVED
    shipping: Money | None = None
    tax: Money | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PurchaseUpdate:
    """Header fields; None keeps the current value. Lines change through their own functions."""

    vendor: str | None = None
    ordered_on: date | None = None
    shipping: Money | None = None
    tax: Money | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PurchaseLineInfo:
    id: str
    purchase_id: str
    catalog_item_id: str
    qty: int
    unit_price: Money
    received_qty: int

    @property
    def total(self) -> Money:
        return self.unit_price * self.qty

    @property
    def remaining(self) -> int:
        return self.qty - self.received_qty


@dataclass(frozen=True, slots=True)
class PurchaseInfo:
    id: str
    vendor: str
    ordered_on: date
    status: PurchaseStatus
    currency: str
    shipping: Money
    tax: Money
    notes: str
    lines: tuple[PurchaseLineInfo, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def total(self) -> Money:
        """Lines plus shipping and tax. Shipping and tax stay on the header, never per line."""
        total = self.shipping + self.tax
        for line in self.lines:
            total = total + line.total
        return total

    @property
    def open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def remaining_qty(self) -> int:
        return sum(line.remaining for line in self.lines)


def _line_info(row: PurchaseLine, currency: str) -> PurchaseLineInfo:
    return PurchaseLineInfo(
        id=row.id,
        purchase_id=row.purchase_id,
        catalog_item_id=row.catalog_item_id,
        qty=row.qty,
        unit_price=Money(row.unit_price, currency),
        received_qty=row.received_qty,
    )


def _info(row: Purchase, lines: list[PurchaseLine]) -> PurchaseInfo:
    return PurchaseInfo(
        id=row.id,
        vendor=row.vendor,
        ordered_on=row.ordered_on,
        status=PurchaseStatus(row.status),
        currency=row.currency,
        shipping=Money(row.shipping, row.currency),
        tax=Money(row.tax, row.currency),
        notes=row.notes,
        lines=tuple(_line_info(line, row.currency) for line in lines),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _lines_of(db: Session, purchase_id: str) -> list[PurchaseLine]:
    return list(
        db.scalars(
            select(PurchaseLine)
            .where(PurchaseLine.purchase_id == purchase_id)
            .order_by(PurchaseLine.position, PurchaseLine.id)
        ).all()
    )


def _load(db: Session, purchase_id: str) -> Purchase:
    row = db.get(Purchase, purchase_id)
    if row is None:
        raise PurchaseNotFoundError(purchase_id)
    return row


def _load_line(db: Session, line_id: str) -> PurchaseLine:
    row = db.get(PurchaseLine, line_id)
    if row is None:
        raise PurchaseLineNotFoundError(line_id)
    return row


def _clean_vendor(vendor: str) -> str:
    clean = " ".join(vendor.split())
    if len(clean) > VENDOR_MAX_LENGTH:
        raise InvalidPurchaseError(f"Vendor must be at most {VENDOR_MAX_LENGTH} characters")
    return clean


def _check_amounts(currency: str, *amounts: Money) -> None:
    for amount in amounts:
        if amount.currency != currency:
            raise CurrencyMismatchError(currency, amount.currency)
        if amount.is_negative:
            raise InvalidPurchaseError("Amounts on a purchase cannot be negative")


def _require_open(purchase: Purchase, action: str) -> None:
    if purchase.status not in {s.value for s in OPEN_STATUSES}:
        raise PurchaseStateError(f"Cannot {action} a {purchase.status} purchase")


def _settle_status(purchase: Purchase, lines: list[PurchaseLine]) -> None:
    """After a receipt: a planned purchase is now at least ordered; fully received closes it."""
    if all(line.received_qty >= line.qty for line in lines):
        purchase.status = PurchaseStatus.RECEIVED.value
    elif purchase.status == PurchaseStatus.PLANNED.value:
        purchase.status = PurchaseStatus.ORDERED.value


def _receive(
    db: Session, purchase: Purchase, line: PurchaseLine, qty: int, occurred_on: date, now: datetime
) -> None:
    wanted = validate_quantity(qty)
    remaining = line.qty - line.received_qty
    if wanted > remaining:
        raise InvalidPurchaseError(f"Only {remaining} of {line.qty} still to receive on this line")
    lock_item(db, line.catalog_item_id)
    record_receipt(
        db,
        item_id=line.catalog_item_id,
        qty=wanted,
        purchase_line_id=line.id,
        occurred_on=occurred_on,
        now=now,
    )
    line.received_qty += wanted
    purchase.updated_at = now


def record_purchase(
    session_factory: SessionFactory,
    principal: Principal,
    command: PurchaseCreate,
    *,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Record a purchase. A ``received`` purchase puts every line into stock at once."""
    require_write(principal)
    currency = workspace.get_currency(session_factory)
    if not command.lines:
        raise InvalidPurchaseError("A purchase needs at least one line")
    vendor = _clean_vendor(command.vendor)
    shipping = command.shipping or Money.zero(currency)
    tax = command.tax or Money.zero(currency)
    _check_amounts(currency, shipping, tax, *(line.unit_price for line in command.lines))
    for line in command.lines:
        validate_quantity(line.qty)
    status = PurchaseStatus(command.status)
    moment = now or utcnow()
    day = command.ordered_on or today()

    with session_factory.begin() as db:
        # Resolve merged items to their target and lock in a fixed order so two purchases
        # never wait on each other.
        item_ids = {
            line.catalog_item_id: load_live_item(db, line.catalog_item_id).id
            for line in command.lines
        }
        for item_id in sorted(set(item_ids.values())):
            lock_item(db, item_id)
        purchase = Purchase(
            id=str(uuid.uuid4()),
            vendor=vendor,
            ordered_on=day,
            status=status.value,
            currency=currency,
            shipping=shipping.amount,
            tax=tax.amount,
            notes=command.notes.strip(),
            created_at=moment,
            updated_at=moment,
        )
        db.add(purchase)
        rows: list[PurchaseLine] = []
        for position, line in enumerate(command.lines):
            row = PurchaseLine(
                id=str(uuid.uuid4()),
                purchase_id=purchase.id,
                catalog_item_id=item_ids[line.catalog_item_id],
                position=position,
                qty=line.qty,
                unit_price=line.unit_price.amount,
                received_qty=0,
            )
            db.add(row)
            rows.append(row)
        db.flush()
        if status is PurchaseStatus.RECEIVED:
            for row in rows:
                _receive(db, purchase, row, row.qty, day, moment)
        return _info(purchase, rows)


def mark_purchased(
    session_factory: SessionFactory,
    principal: Principal,
    item_id: str,
    qty: int,
    unit_price: Money,
    *,
    vendor: str = "",
    ordered_on: date | None = None,
    now: datetime | None = None,
) -> PurchaseInfo:
    """The one-click path: one item, received today, straight into stock."""
    command = PurchaseCreate(
        lines=(PurchaseLineCreate(catalog_item_id=item_id, qty=qty, unit_price=unit_price),),
        vendor=vendor,
        ordered_on=ordered_on,
        status=PurchaseStatus.RECEIVED,
    )
    return record_purchase(session_factory, principal, command, now=now)


def update_purchase(
    session_factory: SessionFactory,
    principal: Principal,
    purchase_id: str,
    command: PurchaseUpdate,
    *,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Change header fields. Cancelled purchases are history and stay as they are."""
    require_write(principal)
    vendor = _clean_vendor(command.vendor) if command.vendor is not None else None
    with session_factory.begin() as db:
        purchase = _load(db, purchase_id)
        if purchase.status == PurchaseStatus.CANCELLED.value:
            raise PurchaseStateError("Cannot edit a cancelled purchase")
        amounts = [a for a in (command.shipping, command.tax) if a is not None]
        _check_amounts(purchase.currency, *amounts)
        if vendor is not None:
            purchase.vendor = vendor
        if command.ordered_on is not None:
            purchase.ordered_on = command.ordered_on
        if command.shipping is not None:
            purchase.shipping = command.shipping.amount
        if command.tax is not None:
            purchase.tax = command.tax.amount
        if command.notes is not None:
            purchase.notes = command.notes.strip()
        purchase.updated_at = now or utcnow()
        return _info(purchase, _lines_of(db, purchase_id))


def add_line(
    session_factory: SessionFactory,
    principal: Principal,
    purchase_id: str,
    command: PurchaseLineCreate,
    *,
    now: datetime | None = None,
) -> PurchaseInfo:
    require_write(principal)
    validate_quantity(command.qty)
    with session_factory.begin() as db:
        purchase = _load(db, purchase_id)
        _require_open(purchase, "add a line to")
        _check_amounts(purchase.currency, command.unit_price)
        item = load_live_item(db, command.catalog_item_id)
        lines = _lines_of(db, purchase_id)
        db.add(
            PurchaseLine(
                id=str(uuid.uuid4()),
                purchase_id=purchase.id,
                catalog_item_id=item.id,
                position=(max((line.position for line in lines), default=-1) + 1),
                qty=command.qty,
                unit_price=command.unit_price.amount,
                received_qty=0,
            )
        )
        purchase.updated_at = now or utcnow()
        db.flush()
        return _info(purchase, _lines_of(db, purchase_id))


def remove_line(
    session_factory: SessionFactory,
    principal: Principal,
    purchase_id: str,
    line_id: str,
    *,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Drop a line nothing has been received on. A purchase keeps at least one line."""
    require_write(principal)
    with session_factory.begin() as db:
        purchase = _load(db, purchase_id)
        _require_open(purchase, "remove a line from")
        line = _load_line(db, line_id)
        if line.purchase_id != purchase.id:
            raise PurchaseLineNotFoundError(line_id)
        if line.received_qty:
            raise PurchaseStateError("Cannot remove a line that has been received on")
        lines = _lines_of(db, purchase_id)
        if len(lines) == 1:
            raise InvalidPurchaseError("A purchase needs at least one line")
        db.delete(line)
        purchase.updated_at = now or utcnow()
        db.flush()
        return _info(purchase, _lines_of(db, purchase_id))


def receive_line(
    session_factory: SessionFactory,
    principal: Principal,
    line_id: str,
    qty: int,
    *,
    occurred_on: date | None = None,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Receive part or all of one line into stock; the remainder stays on order."""
    require_write(principal)
    moment = now or utcnow()
    with session_factory.begin() as db:
        line = _load_line(db, line_id)
        purchase = _load(db, line.purchase_id)
        _require_open(purchase, "receive on")
        _receive(db, purchase, line, qty, occurred_on or today(), moment)
        lines = _lines_of(db, purchase.id)
        _settle_status(purchase, lines)
        return _info(purchase, lines)


def receive_all(
    session_factory: SessionFactory,
    principal: Principal,
    purchase_id: str,
    *,
    occurred_on: date | None = None,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Receive whatever is still outstanding on every line; the purchase becomes ``received``."""
    require_write(principal)
    moment = now or utcnow()
    day = occurred_on or today()
    with session_factory.begin() as db:
        purchase = _load(db, purchase_id)
        _require_open(purchase, "receive")
        lines = _lines_of(db, purchase_id)
        for line in lines:
            if line.received_qty < line.qty:
                _receive(db, purchase, line, line.qty - line.received_qty, day, moment)
        _settle_status(purchase, lines)
        purchase.updated_at = moment
        return _info(purchase, lines)


def set_status(
    session_factory: SessionFactory,
    principal: Principal,
    purchase_id: str,
    status: PurchaseStatus,
    *,
    now: datetime | None = None,
) -> PurchaseInfo:
    """Move between planned, ordered and cancelled. ``received`` goes through ``receive_all``."""
    require_write(principal)
    target = PurchaseStatus(status)
    if target is PurchaseStatus.RECEIVED:
        return receive_all(session_factory, principal, purchase_id, now=now)
    with session_factory.begin() as db:
        purchase = _load(db, purchase_id)
        lines = _lines_of(db, purchase_id)
        received_any = any(line.received_qty for line in lines)
        if purchase.status == PurchaseStatus.RECEIVED.value:
            raise PurchaseStateError("A received purchase stays received; refunds are not in V1")
        if target is PurchaseStatus.CANCELLED and received_any:
            raise PurchaseStateError("Cannot cancel a purchase that has been received on")
        if target is PurchaseStatus.PLANNED and received_any:
            raise PurchaseStateError("A partly received purchase is at least ordered")
        purchase.status = target.value
        purchase.updated_at = now or utcnow()
        return _info(purchase, lines)


def get_purchase(
    session_factory: SessionFactory, principal: Principal, purchase_id: str
) -> PurchaseInfo:
    with session_factory() as db:
        return _info(_load(db, purchase_id), _lines_of(db, purchase_id))


def list_purchases(session_factory: SessionFactory, principal: Principal) -> list[PurchaseInfo]:
    """Newest first."""
    with session_factory() as db:
        rows = db.scalars(
            select(Purchase).order_by(
                Purchase.ordered_on.desc(), Purchase.created_at.desc(), Purchase.id
            )
        ).all()
        return [_info(row, _lines_of(db, row.id)) for row in rows]


def total_since(db: Session, since: date, currency: str) -> Money:
    """Spend that reduces the month-1 opening balance: ordered or received, dated on or after ``since``."""
    lines = db.scalar(
        select(func.coalesce(func.sum(PurchaseLine.qty * PurchaseLine.unit_price), 0))
        .select_from(PurchaseLine)
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .where(Purchase.status.in_(COUNTS_AS_SPENT), Purchase.ordered_on >= since)
    )
    header = db.scalar(
        select(func.coalesce(func.sum(Purchase.shipping + Purchase.tax), 0)).where(
            Purchase.status.in_(COUNTS_AS_SPENT), Purchase.ordered_on >= since
        )
    )
    return Money(int(lines or 0) + int(header or 0), currency)


def on_order_by_item(db: Session) -> dict[str, int]:
    """Unreceived quantity on purchases in ``ordered`` status, per item (ADR-004)."""
    rows = db.execute(
        select(
            PurchaseLine.catalog_item_id,
            func.sum(PurchaseLine.qty - PurchaseLine.received_qty),
        )
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .where(Purchase.status == PurchaseStatus.ORDERED.value)
        .group_by(PurchaseLine.catalog_item_id)
    ).all()
    return {item_id: int(total) for item_id, total in rows if total}
