"""Request and response models shared by the ``/api/v1`` routers.

Money crosses this boundary as a decimal string (``"12.50"``) plus the integer
minor-unit field and the currency, never a float (ADR-003). Inputs accept only
the string form.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from projectbudget.domain.catalog import (
    NAME_MAX_LENGTH,
    SLUG_MAX_LENGTH,
    UNIT_MAX_LENGTH,
    Confidence,
    EstimateOrigin,
    Intent,
    ItemKind,
    MovementReason,
    ReviewState,
    Sharing,
)
from projectbudget.domain.consolidation import Backlog, BacklogRow
from projectbudget.domain.money import Money
from projectbudget.services.catalog import CatalogItemInfo, EstimateInfo
from projectbudget.services.inventory import MovementInfo
from projectbudget.services.projects import ProjectInfo
from projectbudget.services.requirements import RequirementInfo


class Stripped(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class MoneyOut(BaseModel):
    amount: str = Field(description='Decimal string in the workspace currency, e.g. "12.50".')
    minor: int = Field(description="The same amount as an integer in minor units.")
    currency: str

    @classmethod
    def of(cls, money: Money) -> "MoneyOut":
        return cls(amount=str(money), minor=money.amount, currency=money.currency)


# --- projects --------------------------------------------------------------------


class ProjectIn(Stripped):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    notes: str = ""


class ProjectPatch(Stripped):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    notes: str | None = None
    archived: bool | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    archived: bool
    notes: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, info: ProjectInfo) -> "ProjectOut":
        return cls(
            id=info.id,
            name=info.name,
            archived=info.archived,
            notes=info.notes,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )


# --- catalog ---------------------------------------------------------------------


class CatalogItemIn(Stripped):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    kind: ItemKind = ItemKind.CONSUMABLE
    unit: str = Field(default="each", min_length=1, max_length=UNIT_MAX_LENGTH)
    slug: str | None = Field(default=None, min_length=1, max_length=SLUG_MAX_LENGTH)
    review_state: ReviewState = ReviewState.CONFIRMED


class CatalogItemPatch(Stripped):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    kind: ItemKind | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=UNIT_MAX_LENGTH)
    review_state: ReviewState | None = None


class CatalogItemOut(BaseModel):
    id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    review_state: ReviewState
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, info: CatalogItemInfo) -> "CatalogItemOut":
        return cls(
            id=info.id,
            slug=info.slug,
            name=info.name,
            kind=info.kind,
            unit=info.unit,
            review_state=info.review_state,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )


class EstimateIn(Stripped):
    price: StrictStr = Field(
        description='Unit price as a decimal string in the workspace currency, e.g. "12.50".'
    )
    as_of: date | None = None
    confidence: Confidence = Confidence.MEDIUM
    origin: EstimateOrigin = EstimateOrigin.MANUAL


class EstimateOut(BaseModel):
    id: str
    catalog_item_id: str
    price: MoneyOut
    as_of: date
    confidence: Confidence
    origin: EstimateOrigin
    created_at: datetime

    @classmethod
    def of(cls, info: EstimateInfo) -> "EstimateOut":
        return cls(
            id=info.id,
            catalog_item_id=info.catalog_item_id,
            price=MoneyOut.of(info.price),
            as_of=info.as_of,
            confidence=info.confidence,
            origin=info.origin,
            created_at=info.created_at,
        )


class AdjustmentIn(Stripped):
    delta: int = Field(description="Signed change to the count; non-zero.")
    occurred_on: date | None = None
    note: str = ""


class MovementOut(BaseModel):
    id: str
    catalog_item_id: str
    delta_qty: int
    reason: MovementReason
    requirement_id: str | None
    occurred_on: date
    note: str
    created_at: datetime

    @classmethod
    def of(cls, info: MovementInfo) -> "MovementOut":
        return cls(
            id=info.id,
            catalog_item_id=info.catalog_item_id,
            delta_qty=info.delta_qty,
            reason=info.reason,
            requirement_id=info.requirement_id,
            occurred_on=info.occurred_on,
            note=info.note,
            created_at=info.created_at,
        )


# --- requirements ----------------------------------------------------------------


class RequirementIn(Stripped):
    project_id: str
    qty: int
    priority: int = 2
    catalog_item_id: str | None = None
    item_text: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    unit: str | None = Field(default=None, max_length=UNIT_MAX_LENGTH)
    sharing: Sharing = Sharing.SHARED
    notes: str = ""


class RequirementPatch(Stripped):
    qty: int | None = None
    priority: int | None = None
    sort_order: int | None = None
    sharing: Sharing | None = None
    notes: str | None = None


class IntentIn(Stripped):
    intent: Intent
    archive_reason: str | None = None


class ConsumeIn(Stripped):
    qty: int
    occurred_on: date | None = None
    note: str = ""


class RequirementOut(BaseModel):
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

    @classmethod
    def of(cls, info: RequirementInfo) -> "RequirementOut":
        return cls(
            id=info.id,
            project_id=info.project_id,
            catalog_item_id=info.catalog_item_id,
            qty=info.qty,
            priority=info.priority,
            sort_order=info.sort_order,
            intent=info.intent,
            archive_reason=info.archive_reason,
            sharing=info.sharing,
            notes=info.notes,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )


# --- backlog ---------------------------------------------------------------------


class BacklogRowOut(BaseModel):
    item_id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    priority: int
    needed: int
    on_hand: int
    gross_shortfall: int
    on_order: int
    net_shortfall: int
    unit_price: MoneyOut | None
    line_total: MoneyOut | None
    price_as_of: date | None
    shared: bool = Field(description="A shared reusable requirement contributes (ADR-004).")
    requirement_ids: list[str]
    project_ids: list[str]

    @classmethod
    def of(cls, row: BacklogRow) -> "BacklogRowOut":
        return cls(
            item_id=row.item.id,
            slug=row.item.slug,
            name=row.item.name,
            kind=row.item.kind,
            unit=row.item.unit,
            priority=row.priority,
            needed=row.needed,
            on_hand=row.on_hand,
            gross_shortfall=row.gross_shortfall,
            on_order=row.on_order,
            net_shortfall=row.net_shortfall,
            unit_price=MoneyOut.of(row.unit_price) if row.unit_price else None,
            line_total=MoneyOut.of(row.line_total) if row.line_total else None,
            price_as_of=row.item.price_as_of,
            shared=row.shared,
            requirement_ids=list(row.requirement_ids),
            project_ids=list(row.project_ids),
        )


class BacklogOut(BaseModel):
    currency: str
    total: MoneyOut = Field(
        description="Sum of priced rows; a lower bound when anything is unpriced."
    )
    lower_bound: bool
    unpriced_count: int
    rows: list[BacklogRowOut]
    unpriced: list[BacklogRowOut]

    @classmethod
    def of(cls, backlog: Backlog) -> "BacklogOut":
        return cls(
            currency=backlog.currency,
            total=MoneyOut.of(backlog.total),
            lower_bound=backlog.is_lower_bound,
            unpriced_count=backlog.unpriced_count,
            rows=[BacklogRowOut.of(row) for row in backlog.rows],
            unpriced=[BacklogRowOut.of(row) for row in backlog.unpriced],
        )


class OrderIn(BaseModel):
    item_ids: list[str]
