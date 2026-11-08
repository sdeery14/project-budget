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
    PurchaseStatus,
    ReviewState,
    Sharing,
)
from projectbudget.domain.consolidation import Backlog, BacklogRow
from projectbudget.domain.forecast import (
    DEFAULT_HORIZON_MONTHS,
    Forecast,
    ItemOutcome,
    ItemStatus,
    MonthRecord,
    Schedule,
    ScheduledPurchase,
)
from projectbudget.domain.money import Money
from projectbudget.services.budget import OverrideInfo, PlanInfo
from projectbudget.services.catalog import CatalogItemInfo, EstimateInfo
from projectbudget.services.inventory import MovementInfo
from projectbudget.services.projects import ProjectInfo
from projectbudget.services.purchases import PurchaseInfo, PurchaseLineInfo
from projectbudget.services.requirements import RequirementInfo
from projectbudget.services.transfer import ImportSummary


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
    merged_into_id: str | None = None
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
            merged_into_id=info.merged_into_id,
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


# --- budget plan -----------------------------------------------------------------


class OverrideIn(Stripped):
    month: date = Field(description="Any day in the month; normalised to the first.")
    delta: StrictStr = Field(description='Signed decimal string, e.g. "-120.00".')
    label: str = Field(min_length=1, max_length=100)


class OverrideOut(BaseModel):
    id: str
    month: date
    delta: MoneyOut
    label: str

    @classmethod
    def of(cls, info: OverrideInfo) -> "OverrideOut":
        return cls(id=info.id, month=info.month, delta=MoneyOut.of(info.delta), label=info.label)


class PlanIn(Stripped):
    earmarked_funds: StrictStr = Field(description="Balance on balance_as_of as a decimal string.")
    protected_reserve: StrictStr = "0"
    monthly_contribution: StrictStr = "0"
    horizon_months: int = DEFAULT_HORIZON_MONTHS
    contingency_pct: int = 0
    balance_as_of: date | None = Field(
        default=None, description="Omit to reset to today when the balance changed."
    )


class PlanOut(BaseModel):
    currency: str
    earmarked_funds: MoneyOut
    balance_as_of: date
    protected_reserve: MoneyOut
    monthly_contribution: MoneyOut
    horizon_months: int
    contingency_pct: int
    updated_at: datetime
    overrides: list[OverrideOut]

    @classmethod
    def of(cls, info: PlanInfo) -> "PlanOut":
        return cls(
            currency=info.currency,
            earmarked_funds=MoneyOut.of(info.earmarked_funds),
            balance_as_of=info.balance_as_of,
            protected_reserve=MoneyOut.of(info.protected_reserve),
            monthly_contribution=MoneyOut.of(info.monthly_contribution),
            horizon_months=info.horizon_months,
            contingency_pct=info.contingency_pct,
            updated_at=info.updated_at,
            overrides=[OverrideOut.of(o) for o in info.overrides],
        )


# --- purchases -------------------------------------------------------------------


class PurchaseLineIn(Stripped):
    catalog_item_id: str
    qty: int
    unit_price: StrictStr


class PurchaseIn(Stripped):
    lines: list[PurchaseLineIn] = Field(min_length=1)
    vendor: str = ""
    ordered_on: date | None = None
    status: PurchaseStatus = PurchaseStatus.RECEIVED
    shipping: StrictStr | None = None
    tax: StrictStr | None = None
    notes: str = ""


class PurchasePatch(Stripped):
    vendor: str | None = None
    ordered_on: date | None = None
    shipping: StrictStr | None = None
    tax: StrictStr | None = None
    notes: str | None = None


class PurchaseStatusIn(Stripped):
    status: PurchaseStatus


class ReceiveIn(Stripped):
    occurred_on: date | None = None


class ReceiveLineIn(Stripped):
    qty: int
    occurred_on: date | None = None


class MergeIn(Stripped):
    target_id: str = Field(
        description="The item that survives; this item becomes a redirect to it."
    )


class ImportSummaryOut(BaseModel):
    projects: int
    catalog_items: int
    requirements: int
    price_estimates: int
    inventory_movements: int
    purchases: int
    purchase_lines: int
    budget_plan: bool
    replaced: bool

    @classmethod
    def of(cls, summary: ImportSummary) -> "ImportSummaryOut":
        return cls(
            projects=summary.projects,
            catalog_items=summary.catalog_items,
            requirements=summary.requirements,
            price_estimates=summary.price_estimates,
            inventory_movements=summary.inventory_movements,
            purchases=summary.purchases,
            purchase_lines=summary.purchase_lines,
            budget_plan=summary.budget_plan,
            replaced=summary.replaced,
        )


class MarkPurchasedIn(Stripped):
    item_id: str
    qty: int
    unit_price: StrictStr
    vendor: str = ""


class PurchaseLineOut(BaseModel):
    id: str
    catalog_item_id: str
    qty: int
    unit_price: MoneyOut
    received_qty: int
    total: MoneyOut

    @classmethod
    def of(cls, info: PurchaseLineInfo) -> "PurchaseLineOut":
        return cls(
            id=info.id,
            catalog_item_id=info.catalog_item_id,
            qty=info.qty,
            unit_price=MoneyOut.of(info.unit_price),
            received_qty=info.received_qty,
            total=MoneyOut.of(info.total),
        )


class PurchaseOut(BaseModel):
    id: str
    vendor: str
    ordered_on: date
    status: PurchaseStatus
    currency: str
    shipping: MoneyOut
    tax: MoneyOut
    total: MoneyOut
    notes: str
    lines: list[PurchaseLineOut]
    created_at: datetime

    @classmethod
    def of(cls, info: PurchaseInfo) -> "PurchaseOut":
        return cls(
            id=info.id,
            vendor=info.vendor,
            ordered_on=info.ordered_on,
            status=info.status,
            currency=info.currency,
            shipping=MoneyOut.of(info.shipping),
            tax=MoneyOut.of(info.tax),
            total=MoneyOut.of(info.total),
            notes=info.notes,
            lines=[PurchaseLineOut.of(line) for line in info.lines],
            created_at=info.created_at,
        )


# --- forecast --------------------------------------------------------------------

DISCLAIMER = "Planning projection from your inputs; not financial advice."


class ScheduledPurchaseOut(BaseModel):
    item_id: str
    name: str
    qty: int
    cost: MoneyOut
    stale: bool

    @classmethod
    def of(cls, purchase: ScheduledPurchase) -> "ScheduledPurchaseOut":
        return cls(
            item_id=purchase.item.item_id,
            name=purchase.item.name,
            qty=purchase.item.qty,
            cost=MoneyOut.of(purchase.cost),
            stale=purchase.stale,
        )


class MonthOverrideOut(BaseModel):
    month: date
    delta: MoneyOut
    label: str


class MonthOut(BaseModel):
    month: date
    opening: MoneyOut
    contribution: MoneyOut
    overrides: list[MonthOverrideOut]
    purchases: list[ScheduledPurchaseOut]
    closing: MoneyOut
    blocked: bool
    stale: bool

    @classmethod
    def of(cls, record: MonthRecord) -> "MonthOut":
        return cls(
            month=record.month,
            opening=MoneyOut.of(record.opening),
            contribution=MoneyOut.of(record.contribution),
            overrides=[
                MonthOverrideOut(month=o.month, delta=MoneyOut.of(o.delta), label=o.label)
                for o in record.overrides
            ],
            purchases=[ScheduledPurchaseOut.of(p) for p in record.purchases],
            closing=MoneyOut.of(record.closing),
            blocked=record.blocked,
            stale=record.stale,
        )


class ItemOutcomeOut(BaseModel):
    item_id: str
    name: str
    unit: str
    priority: int
    qty: int
    unit_price: MoneyOut
    cost: MoneyOut
    status: ItemStatus
    month: date | None
    stale: bool

    @classmethod
    def of(cls, outcome: ItemOutcome) -> "ItemOutcomeOut":
        return cls(
            item_id=outcome.item.item_id,
            name=outcome.item.name,
            unit=outcome.item.unit,
            priority=outcome.item.priority,
            qty=outcome.item.qty,
            unit_price=MoneyOut.of(outcome.item.unit_price),
            cost=MoneyOut.of(outcome.cost),
            status=outcome.status,
            month=outcome.month,
            stale=outcome.stale,
        )


class ScheduleOut(BaseModel):
    contingency_pct: int
    opening_balance: MoneyOut
    blocked: bool
    recovery_month: date | None
    scheduled_total: MoneyOut
    months: list[MonthOut]
    items: list[ItemOutcomeOut]

    @classmethod
    def of(cls, sched: Schedule) -> "ScheduleOut":
        return cls(
            contingency_pct=sched.contingency_pct,
            opening_balance=MoneyOut.of(sched.opening_balance),
            blocked=sched.blocked,
            recovery_month=sched.recovery_month,
            scheduled_total=MoneyOut.of(sched.scheduled_total),
            months=[MonthOut.of(m) for m in sched.months],
            items=[ItemOutcomeOut.of(i) for i in sched.items],
        )


class ForecastOut(BaseModel):
    today: date
    balance_as_of: date
    balance_stale: bool
    recent_purchases_total: MoneyOut
    lower_bound: bool
    unpriced_count: int
    unpriced_ahead: bool
    optimistic: ScheduleOut
    conservative: ScheduleOut
    disclaimer: str = DISCLAIMER

    @classmethod
    def of(cls, result: Forecast) -> "ForecastOut":
        return cls(
            today=result.today,
            balance_as_of=result.plan.balance_as_of,
            balance_stale=result.balance_stale,
            recent_purchases_total=MoneyOut.of(result.recent_purchases_total),
            lower_bound=result.lower_bound,
            unpriced_count=result.unpriced_count,
            unpriced_ahead=result.unpriced_ahead,
            optimistic=ScheduleOut.of(result.optimistic),
            conservative=ScheduleOut.of(result.conservative),
        )
