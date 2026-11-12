"""Catalog items and their price estimates (RFC v3 §4, ADR-003, ADR-004).

Items come from free text ("M5 bolts" while adding a requirement) as often as
from the catalog page, so creation by name is a first-class path and such items
start ``unreviewed``. A missing estimate is missing, never zero.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
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
from projectbudget.persistence.models import (
    BacklogOrder,
    CatalogItem,
    InventoryMovement,
    PriceEstimate,
    PurchaseLine,
    Requirement,
)
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


class MergeError(ValueError):
    """Items cannot be merged: same item, different unit or kind, or already merged."""


class MergedItemError(ValueError):
    """A merged item is a redirect; edit the item it points to instead."""

    def __init__(self, item_id: str, target_id: str) -> None:
        super().__init__(f"Catalog item {item_id!r} was merged into {target_id!r}")
        self.item_id = item_id
        self.target_id = target_id


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
    merged_into_id: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def merged(self) -> bool:
        return self.merged_into_id is not None


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
        merged_into_id=row.merged_into_id,
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


def resolve_live(db: Session, row: CatalogItem) -> CatalogItem:
    """Follow merge redirects to the item that is actually in use."""
    seen = {row.id}
    while row.merged_into_id is not None:
        target = db.get(CatalogItem, row.merged_into_id)
        if target is None or target.id in seen:
            return row
        seen.add(target.id)
        row = target
    return row


def load_live_item(db: Session, item_id: str) -> CatalogItem:
    """Fetch an item by id, following merge redirects (an old id keeps working)."""
    return resolve_live(db, load_item(db, item_id))


def find_live_by_slug(db: Session, slug: str) -> CatalogItem | None:
    """Fetch an item by slug, following merge redirects (an old manifest slug keeps working)."""
    row = find_by_slug(db, slug)
    return resolve_live(db, row) if row is not None else None


def unit_in_use(db: Session, item_id: str) -> bool:
    """True when a requirement, estimate, movement or purchase line is in this item's unit.

    Movements count even when they net to zero, and purchase lines count in every
    status: the history "received 10, used 10" is in metres and must stay in metres.
    """
    probes = (
        select(Requirement.id).where(Requirement.catalog_item_id == item_id),
        select(PriceEstimate.id).where(PriceEstimate.catalog_item_id == item_id),
        select(InventoryMovement.id).where(InventoryMovement.catalog_item_id == item_id),
        select(PurchaseLine.id).where(PurchaseLine.catalog_item_id == item_id),
    )
    return any(db.scalar(probe.limit(1)) is not None for probe in probes)


def lock_item(db: Session, item_id: str) -> CatalogItem:
    """Take a write lock on the item row for the rest of the transaction, then load it.

    Stock checks are read-then-write ("is there enough? then record the movement"),
    so two concurrent callers must not both read the same count. A no-op UPDATE of
    the item row serialises them on both backends: PostgreSQL holds the row lock
    until commit, and SQLite takes its database write lock at this statement, so
    every read that follows sees the other writer's committed movements. The
    lock must be taken before any read the decision depends on.
    """
    result = cast(
        CursorResult[Any],
        db.execute(
            update(CatalogItem)
            .where(CatalogItem.id == item_id)
            .values(updated_at=CatalogItem.updated_at)
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount == 0:
        raise CatalogItemNotFoundError(item_id)
    return load_item(db, item_id)


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
    existing = find_live_by_slug(db, slugify(name))
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


def confirm(db: Session, row: CatalogItem, now: datetime) -> None:
    """The owner has looked at this item, so manifests may no longer change its kind or unit.

    Called when the owner edits the item, counts its stock or buys it (ADR-012, #36); the
    protection itself is ADR-007's and is unchanged. A sync never calls this.
    """
    if row.review_state != ReviewState.CONFIRMED.value:
        row.review_state = ReviewState.CONFIRMED.value
        row.updated_at = now


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
        row = lock_item(db, item_id) if unit is not None else load_item(db, item_id)
        if row.merged_into_id is not None:
            raise MergedItemError(row.id, row.merged_into_id)
        if name is not None:
            row.name = name
        if command.kind is not None:
            row.kind = ItemKind(command.kind).value
        if unit is not None and unit != row.unit:
            if unit_in_use(db, row.id):
                raise UnitInUseError(row.id, row.unit)
            row.unit = unit
        if command.review_state is not None:
            row.review_state = ReviewState(command.review_state).value
        elif name is not None or command.kind is not None or unit is not None:
            confirm(db, row, now or utcnow())  # editing by hand is reviewing (#36)
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
        row = find_live_by_slug(db, slug)
        return _item_info(row) if row is not None else None


def list_items(
    session_factory: SessionFactory,
    principal: Principal,
    *,
    include_merged: bool = False,
    review_state: ReviewState | None = None,
) -> list[CatalogItemInfo]:
    query = select(CatalogItem).order_by(CatalogItem.name, CatalogItem.id)
    if not include_merged:
        query = query.where(CatalogItem.merged_into_id.is_(None))
    if review_state is not None:
        query = query.where(CatalogItem.review_state == ReviewState(review_state).value)
    with session_factory() as db:
        return [_item_info(row) for row in db.scalars(query).all()]


def merge_items(
    session_factory: SessionFactory,
    principal: Principal,
    source_id: str,
    target_id: str,
    *,
    now: datetime | None = None,
) -> CatalogItemInfo:
    """Fold ``source`` into ``target``: every reference moves in one transaction and a redirect stays.

    Requirements, purchase lines, inventory movements and price estimates are re-pointed;
    the source keeps its slug and ``merged_into_id`` so old ids and manifest slugs still
    resolve. Units and kinds must match: quantities are in the unit, and consolidation
    rules differ by kind.
    """
    require_write(principal)
    if source_id == target_id:
        raise MergeError("An item cannot be merged into itself")
    moment = now or utcnow()
    with session_factory.begin() as db:
        for item_id in sorted((source_id, target_id)):
            lock_item(db, item_id)
        source, target = load_item(db, source_id), load_item(db, target_id)
        if source.merged_into_id is not None:
            raise MergedItemError(source.id, source.merged_into_id)
        if target.merged_into_id is not None:
            raise MergedItemError(target.id, target.merged_into_id)
        if source.unit != target.unit:
            raise MergeError(
                f"Units differ ({source.unit!r} vs {target.unit!r}); nothing is converted"
            )
        if source.kind != target.kind:
            raise MergeError(f"Kinds differ ({source.kind} vs {target.kind})")
        for table in (Requirement, PurchaseLine, InventoryMovement, PriceEstimate):
            db.execute(
                update(table)
                .where(table.catalog_item_id == source.id)
                .values(catalog_item_id=target.id)
                .execution_options(synchronize_session=False)
            )
        source_order = db.get(BacklogOrder, source.id)
        if source_order is not None:
            if db.get(BacklogOrder, target.id) is None:
                db.add(BacklogOrder(catalog_item_id=target.id, position=source_order.position))
            db.delete(source_order)
        source.merged_into_id = target.id
        source.updated_at = moment
        if source.review_state == ReviewState.CONFIRMED.value:
            target.review_state = ReviewState.CONFIRMED.value
        target.updated_at = moment
        db.flush()
        return _item_info(target)


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
        item = load_live_item(db, item_id)
        row = PriceEstimate(
            id=str(uuid.uuid4()),
            catalog_item_id=item.id,
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
