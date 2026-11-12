"""Requirements: what a project still wants (RFC v3 §4, ADR-004).

A requirement always references a catalog item; free text is resolved to one
(creating it if needed) inside the same transaction. The unit a user types must
match the item's unit; units are never converted. Intent changes keep the row,
so cancelled and archived requirements remain history.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from projectbudget.domain.catalog import (
    ARCHIVE_REASON_MAX_LENGTH,
    Intent,
    Sharing,
    validate_priority,
    validate_quantity,
    validate_unit,
)
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import CatalogItem, Requirement
from projectbudget.persistence.session import SessionFactory
from projectbudget.services.catalog import find_or_create_from_text, load_live_item
from projectbudget.services.clock import utcnow
from projectbudget.services.guards import NotFoundError, require_write
from projectbudget.services.projects import load_project


class RequirementNotFoundError(NotFoundError):
    entity = "Requirement"


class UnitMismatchError(ValueError):
    def __init__(self, given: str, expected: str) -> None:
        super().__init__(f"Unit {given!r} does not match the catalog item's unit {expected!r}")
        self.given = given
        self.expected = expected


class ItemReferenceError(ValueError):
    """Exactly one of ``catalog_item_id`` and ``item_text`` must be given."""


class InvalidArchiveReasonError(ValueError):
    """Too long, or given for an intent other than archived."""


@dataclass(frozen=True, slots=True)
class RequirementInfo:
    id: str
    project_id: str
    catalog_item_id: str
    qty: int
    priority: int
    sort_order: int
    intent: Intent
    archive_reason: str | None
    sharing: Sharing
    notes: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RequirementCreate:
    """Name the item by id or by free text, never both. ``unit`` is checked, not stored."""

    project_id: str
    qty: int
    priority: int = 2
    catalog_item_id: str | None = None
    item_text: str | None = None
    unit: str | None = None
    sharing: Sharing = Sharing.SHARED
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RequirementUpdate:
    """Fields left as None keep their value. Intent changes go through ``set_intent``."""

    qty: int | None = None
    priority: int | None = None
    sort_order: int | None = None
    sharing: Sharing | None = None
    notes: str | None = None


def _info(row: Requirement) -> RequirementInfo:
    return RequirementInfo(
        id=row.id,
        project_id=row.project_id,
        catalog_item_id=row.catalog_item_id,
        qty=row.qty,
        priority=row.priority,
        sort_order=row.sort_order,
        intent=Intent(row.intent),
        archive_reason=row.archive_reason,
        sharing=Sharing(row.sharing),
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def load_requirement(db: Session, requirement_id: str) -> Requirement:
    row = db.get(Requirement, requirement_id)
    if row is None:
        raise RequirementNotFoundError(requirement_id)
    return row


def _resolve_item(
    db: Session, command: RequirementCreate, unit: str | None, now: datetime
) -> CatalogItem:
    if (command.catalog_item_id is None) == (command.item_text is None):
        raise ItemReferenceError("Give either catalog_item_id or item_text, not both or neither")
    if command.catalog_item_id is not None:
        item = load_live_item(db, command.catalog_item_id)
    elif command.item_text is not None:
        item = find_or_create_from_text(db, command.item_text, unit=unit, now=now)
    if unit is not None and unit != item.unit:
        raise UnitMismatchError(unit, item.unit)
    return item


def _next_sort_order(db: Session, project_id: str) -> int:
    current = db.scalar(
        select(func.max(Requirement.sort_order)).where(Requirement.project_id == project_id)
    )
    return 0 if current is None else current + 1


def add_requirement(
    session_factory: SessionFactory,
    principal: Principal,
    command: RequirementCreate,
    *,
    now: datetime | None = None,
) -> RequirementInfo:
    require_write(principal)
    qty = validate_quantity(command.qty)
    priority = validate_priority(command.priority)
    unit = validate_unit(command.unit) if command.unit is not None else None
    sharing = Sharing(command.sharing)
    moment = now or utcnow()
    with session_factory.begin() as db:
        project = load_project(db, command.project_id)
        item = _resolve_item(db, command, unit, moment)
        row = Requirement(
            id=str(uuid.uuid4()),
            project_id=project.id,
            catalog_item_id=item.id,
            qty=qty,
            priority=priority,
            sort_order=_next_sort_order(db, project.id),
            intent=Intent.ACTIVE.value,
            archive_reason=None,
            sharing=sharing.value,
            notes=command.notes.strip(),
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        db.flush()
        return _info(row)


def update_requirement(
    session_factory: SessionFactory,
    principal: Principal,
    requirement_id: str,
    command: RequirementUpdate,
    *,
    now: datetime | None = None,
) -> RequirementInfo:
    require_write(principal)
    qty = validate_quantity(command.qty) if command.qty is not None else None
    priority = validate_priority(command.priority) if command.priority is not None else None
    with session_factory.begin() as db:
        row = load_requirement(db, requirement_id)
        if qty is not None:
            row.qty = qty
        if priority is not None:
            row.priority = priority
        if command.sort_order is not None:
            row.sort_order = command.sort_order
        if command.sharing is not None:
            row.sharing = Sharing(command.sharing).value
        if command.notes is not None:
            row.notes = command.notes.strip()
        row.updated_at = now or utcnow()
        return _info(row)


def set_intent(
    session_factory: SessionFactory,
    principal: Principal,
    requirement_id: str,
    intent: Intent,
    *,
    archive_reason: str | None = None,
    now: datetime | None = None,
) -> RequirementInfo:
    """Change what the owner wants. The row and its history always survive."""
    require_write(principal)
    target = Intent(intent)
    reason = archive_reason.strip() if archive_reason else None
    if reason is not None and target is not Intent.ARCHIVED:
        raise InvalidArchiveReasonError("An archive reason only goes with the archived intent")
    if reason is not None and len(reason) > ARCHIVE_REASON_MAX_LENGTH:
        msg = f"Archive reason must be at most {ARCHIVE_REASON_MAX_LENGTH} characters"
        raise InvalidArchiveReasonError(msg)
    with session_factory.begin() as db:
        row = load_requirement(db, requirement_id)
        row.intent = target.value
        row.archive_reason = reason if target is Intent.ARCHIVED else None
        row.updated_at = now or utcnow()
        return _info(row)


def get_requirement(
    session_factory: SessionFactory, principal: Principal, requirement_id: str
) -> RequirementInfo:
    with session_factory() as db:
        return _info(load_requirement(db, requirement_id))


def any_active(session_factory: SessionFactory, principal: Principal) -> bool:
    """Whether any project has an active requirement at all; the backlog's empty state needs it."""
    with session_factory() as db:
        return (
            db.scalar(
                select(Requirement.id).where(Requirement.intent == Intent.ACTIVE.value).limit(1)
            )
            is not None
        )


def list_requirements(
    session_factory: SessionFactory,
    principal: Principal,
    project_id: str,
    *,
    include_inactive: bool = False,
) -> list[RequirementInfo]:
    """A project's requirements in its own order; by default only ``active`` ones."""
    query = (
        select(Requirement)
        .where(Requirement.project_id == project_id)
        .order_by(Requirement.sort_order, Requirement.created_at, Requirement.id)
    )
    if not include_inactive:
        query = query.where(Requirement.intent == Intent.ACTIVE.value)
    with session_factory() as db:
        load_project(db, project_id)
        return [_info(row) for row in db.scalars(query).all()]
