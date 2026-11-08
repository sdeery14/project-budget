"""Purchases: facts about money spent or committed (RFC v3 §4). Never touched by sync.

This PR carries the minimal path the forecast needs: record a purchase with
lines, receive it into stock, and sum recent spend. Partial receipt and
editing arrive with the full purchases milestone (PR6).
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
from projectbudget.services.catalog import lock_item
from projectbudget.services.clock import today, utcnow
from projectbudget.services.guards import NotFoundError, require_write
from projectbudget.services.inventory import record_receipt

VENDOR_MAX_LENGTH = 200
COUNTS_AS_SPENT = (PurchaseStatus.ORDERED.value, PurchaseStatus.RECEIVED.value)


class PurchaseNotFoundError(NotFoundError):
    entity = "Purchase"


class InvalidPurchaseError(ValueError):
    """No lines, a negative header amount, a negative unit price or a vendor that is too long."""


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
    vendor = " ".join(command.vendor.split())
    if len(vendor) > VENDOR_MAX_LENGTH:
        raise InvalidPurchaseError(f"Vendor must be at most {VENDOR_MAX_LENGTH} characters")
    shipping = command.shipping or Money.zero(currency)
    tax = command.tax or Money.zero(currency)
    for amount in (shipping, tax, *(line.unit_price for line in command.lines)):
        if amount.currency != currency:
            raise CurrencyMismatchError(currency, amount.currency)
        if amount.is_negative:
            raise InvalidPurchaseError("Amounts on a purchase cannot be negative")
    for line in command.lines:
        validate_quantity(line.qty)
    status = PurchaseStatus(command.status)
    moment = now or utcnow()
    day = command.ordered_on or today()
    received = status is PurchaseStatus.RECEIVED

    with session_factory.begin() as db:
        # Lock items in a fixed order so two purchases never wait on each other.
        for item_id in sorted({line.catalog_item_id for line in command.lines}):
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
                catalog_item_id=line.catalog_item_id,
                position=position,
                qty=line.qty,
                unit_price=line.unit_price.amount,
                received_qty=line.qty if received else 0,
            )
            db.add(row)
            rows.append(row)
        db.flush()
        if received:
            for row in rows:
                record_receipt(
                    db,
                    item_id=row.catalog_item_id,
                    qty=row.qty,
                    purchase_line_id=row.id,
                    occurred_on=day,
                    now=moment,
                )
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


def get_purchase(
    session_factory: SessionFactory, principal: Principal, purchase_id: str
) -> PurchaseInfo:
    with session_factory() as db:
        row = db.get(Purchase, purchase_id)
        if row is None:
            raise PurchaseNotFoundError(purchase_id)
        return _info(row, _lines_of(db, purchase_id))


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
