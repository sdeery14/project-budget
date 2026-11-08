"""Catalog items and their price estimates (RFC v3 §4, ADR-003, ADR-004).

Items come from free text ("M5 bolts" while adding a requirement) as often as
from the catalog page, so creation by name is a first-class path and such items
start ``unreviewed``. A missing estimate is missing, never zero.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import (
    DEFAULT_UNIT,
    Confidence,
    EstimateOrigin,
    ItemKind,
    ReviewState,
    slugify,
    validate_name,
    validate_slug,
    validate_unit,
)
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import CatalogItem, PriceEstimate, Requirement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.clock import today, utcnow
from projectbudget.services.guards import NotFoundError, require_write


class CatalogItemNotFoundError(NotFoundError):
    entity = "Catalog item"


class SlugTakenError(ValueError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Slug {slug!r} is already used by another catalog item")
        self.slug = slug


class NegativeEstimateError(ValueError):
    """A price estimate is never below zero."""


class UnitInUseError(ValueError):
    """The unit cannot change while requirements or estimates are expressed in it.

    Quantities and unit prices are numbers in the item's unit; changing the unit under
    them would silently turn 10 metres at 1.00 a metre into 10 feet at 1.00 a foot.
    Unit conversion is deferred (RFC v3 §2), so the change is refused instead.
    """

    def __init__(self, item_id: str, unit: str) -> None:
        super().__init__(
            f"Catalog item {item_id!r} keeps unit {unit!r} while requirements or "
            "price estimates reference it"
        )
        self.item_id = item_id
        self.unit = unit


@dataclass(frozen=True, slots=True)
class CatalogItemInfo:
    id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    review_state: ReviewState
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CatalogItemCreate:
    name: str
    kind: ItemKind = ItemKind.CONSUMABLE
    unit: str = DEFAULT_UNIT
    slug: str | None = None
    review_state: ReviewState = ReviewState.CONFIRMED


@dataclass(frozen=True, slots=True)
class CatalogItemUpdate:
    """Fields left as None keep their value. The slug is immutable and has no field here."""

    name: str | None = None
    kind: ItemKind | None = None
    unit: str | None = None
    review_state: ReviewState | None = None


@dataclass(frozen=True, slots=True)
class EstimateInfo:
    id: str
    catalog_item_id: str
    price: Money
    as_of: date
    confidence: Confidence
    origin: EstimateOrigin
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EstimateCreate:
    price: Money
    as_of: date | None = None
    confidence: Confidence = Confidence.MEDIUM
    origin: EstimateOrigin = EstimateOrigin.MANUAL


def _item_info(row: CatalogItem) -> CatalogItemInfo:
    return CatalogItemInfo(
        id=row.id,
        slug=row.slug,
        name=row.name,
        kind=ItemKind(row.kind),
        unit=row.unit,
        review_state=ReviewState(row.review_state),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _estimate_info(row: PriceEstimate) -> EstimateInfo:
    return EstimateInfo(
        id=row.id,
        catalog_item_id=row.catalog_item_id,
        price=Money(row.amount, row.currency),
        as_of=row.as_of,
        confidence=Confidence(row.confidence),
        origin=EstimateOrigin(row.origin),
        created_at=row.created_at,
    )


def load_item(db: Session, item_id: str) -> CatalogItem:
    """Fetch an item row inside a caller's transaction, or raise."""
    row = db.get(CatalogItem, item_id)
    if row is None:
        raise CatalogItemNotFoundError(item_id)
    return row


def find_by_slug(db: Session, slug: str) -> CatalogItem | None:
    return db.scalar(select(CatalogItem).where(CatalogItem.slug == slug))


def _is_referenced(db: Session, item_id: str) -> bool:
    """True when any requirement or price estimate is expressed in this item's unit."""
    has_requirement = select(Requirement.id).where(Requirement.catalog_item_id == item_id).limit(1)
    has_estimate = select(PriceEstimate.id).where(PriceEstimate.catalog_item_id == item_id).limit(1)
    return db.scalar(has_requirement) is not None or db.scalar(has_estimate) is not None


def insert_item(db: Session, command: CatalogItemCreate, now: datetime) -> CatalogItem:
    """Add an item inside a caller's transaction; the slug must be free."""
    name = validate_name(command.name)
    slug = validate_slug(command.slug) if command.slug is not None else slugify(name)
    if find_by_slug(db, slug) is not None:
        raise SlugTakenError(slug)
    row = CatalogItem(
        id=str(uuid.uuid4()),
        slug=slug,
        name=name,
        kind=ItemKind(command.kind).value,
        unit=validate_unit(command.unit),
        review_state=ReviewState(command.review_state).value,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def find_or_create_from_text(
    db: Session, text: str, *, unit: str | None, now: datetime
) -> CatalogItem:
    """Resolve free text to the item with that slug, creating an unreviewed consumable if needed."""
    name = validate_name(text)
    existing = find_by_slug(db, slugify(name))
    if existing is not None:
        return existing
    command = CatalogItemCreate(
        name=name,
        kind=ItemKind.CONSUMABLE,
        unit=unit if unit is not None else DEFAULT_UNIT,
        review_state=ReviewState.UNREVIEWED,
    )
    return insert_item(db, command, now)


def create_item(
    session_factory: SessionFactory,
    principal: Principal,
    command: CatalogItemCreate,
    *,
    now: datetime | None = None,
) -> CatalogItemInfo:
    require_write(principal)
    with session_factory.begin() as db:
        return _item_info(insert_item(db, command, now or utcnow()))


def update_item(
    session_factory: SessionFactory,
    principal: Principal,
    item_id: str,
    command: CatalogItemUpdate,
    *,
    now: datetime | None = None,
) -> CatalogItemInfo:
    require_write(principal)
    name = validate_name(command.name) if command.name is not None else None
    unit = validate_unit(command.unit) if command.unit is not None else None
    with session_factory.begin() as db:
        row = load_item(db, item_id)
        if name is not None:
            row.name = name
        if command.kind is not None:
            row.kind = ItemKind(command.kind).value
        if unit is not None and unit != row.unit:
            if _is_referenced(db, row.id):
                raise UnitInUseError(row.id, row.unit)
            row.unit = unit
        if command.review_state is not None:
            row.review_state = ReviewState(command.review_state).value
        row.updated_at = now or utcnow()
        return _item_info(row)


def get_item(
    session_factory: SessionFactory, principal: Principal, item_id: str
) -> CatalogItemInfo:
    with session_factory() as db:
        return _item_info(load_item(db, item_id))


def get_item_by_slug(
    session_factory: SessionFactory, principal: Principal, slug: str
) -> CatalogItemInfo | None:
    with session_factory() as db:
        row = find_by_slug(db, slug)
        return _item_info(row) if row is not None else None


def list_items(session_factory: SessionFactory, principal: Principal) -> list[CatalogItemInfo]:
    with session_factory() as db:
        rows = db.scalars(select(CatalogItem).order_by(CatalogItem.name, CatalogItem.id)).all()
        return [_item_info(row) for row in rows]


# --- price estimates ---------------------------------------------------------------


def add_estimate(
    session_factory: SessionFactory,
    principal: Principal,
    item_id: str,
    command: EstimateCreate,
    *,
    now: datetime | None = None,
) -> EstimateInfo:
    """Record a unit price. The currency must be the workspace currency (ADR-003)."""
    require_write(principal)
    currency = workspace.get_currency(session_factory)
    if command.price.currency != currency:
        raise CurrencyMismatchError(currency, command.price.currency)
    if command.price.is_negative:
        raise NegativeEstimateError(f"Estimate {command.price} is below zero")
    moment = now or utcnow()
    with session_factory.begin() as db:
        load_item(db, item_id)
        row = PriceEstimate(
            id=str(uuid.uuid4()),
            catalog_item_id=item_id,
            amount=command.price.amount,
            currency=command.price.currency,
            as_of=command.as_of or today(),
            confidence=Confidence(command.confidence).value,
            origin=EstimateOrigin(command.origin).value,
            created_at=moment,
        )
        db.add(row)
        db.flush()
        return _estimate_info(row)


def current_estimate_row(db: Session, item_id: str) -> PriceEstimate | None:
    """Latest ``as_of`` wins; ties go to the most recently recorded. None means unpriced."""
    return db.scalar(
        select(PriceEstimate)
        .where(PriceEstimate.catalog_item_id == item_id)
        .order_by(
            PriceEstimate.as_of.desc(), PriceEstimate.created_at.desc(), PriceEstimate.id.desc()
        )
        .limit(1)
    )


def current_estimate(
    session_factory: SessionFactory, principal: Principal, item_id: str
) -> EstimateInfo | None:
    """The estimate in force, or None when the item is unpriced. Never a zero stand-in."""
    with session_factory() as db:
        load_item(db, item_id)
        row = current_estimate_row(db, item_id)
        return _estimate_info(row) if row is not None else None


def list_estimates(
    session_factory: SessionFactory, principal: Principal, item_id: str
) -> list[EstimateInfo]:
    with session_factory() as db:
        load_item(db, item_id)
        rows = db.scalars(
            select(PriceEstimate)
            .where(PriceEstimate.catalog_item_id == item_id)
            .order_by(PriceEstimate.as_of.desc(), PriceEstimate.created_at.desc(), PriceEstimate.id)
        ).all()
        return [_estimate_info(row) for row in rows]
