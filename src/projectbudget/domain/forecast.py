"""The forecast: a pure, deterministic function of plan, backlog, recent spend and today (ADR-006).

``forecast(plan, backlog, recent_purchases_total, today)`` produces two schedules,
optimistic (no contingency) and conservative (contingency applied). Each month
records opening balance, contribution, overrides, purchases and closing balance;
that record is the explanation shown in the UI. Every rule here is a golden test
in ``tests/test_forecast.py``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from projectbudget.domain.consolidation import Backlog
from projectbudget.domain.money import Money, sum_money

DEFAULT_HORIZON_MONTHS = 36
MAX_HORIZON_MONTHS = 120
STALE_ESTIMATE_DAYS = 90
STALE_BALANCE_DAYS = 60

ItemStatus = Literal["scheduled", "beyond_horizon", "not_fundable", "blocked"]


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(first_of_month: date, count: int) -> date:
    index = first_of_month.year * 12 + (first_of_month.month - 1) + count
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True, slots=True)
class MonthOverride:
    """A signed, labeled one-off applied to one month: a bonus, a tax bill, a holiday."""

    month: date
    delta: Money
    label: str


@dataclass(frozen=True, slots=True)
class BudgetPlan:
    """``earmarked_funds`` is the balance on ``balance_as_of``; spend since then is subtracted."""

    currency: str
    earmarked_funds: Money
    balance_as_of: date
    protected_reserve: Money
    monthly_contribution: Money
    horizon_months: int = DEFAULT_HORIZON_MONTHS
    contingency_pct: int = 0
    overrides: tuple[MonthOverride, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One backlog row as one indivisible purchase: net shortfall at the latest unit estimate."""

    item_id: str
    name: str
    unit: str
    priority: int
    qty: int
    unit_price: Money
    price_as_of: date

    def cost(self, contingency_pct: int) -> Money:
        """Quantity times unit price, marked up by the contingency and rounded up to a minor unit."""
        base = self.qty * self.unit_price.amount
        marked_up = -(-(base * (100 + contingency_pct)) // 100)
        return Money(marked_up, self.unit_price.currency)

    def stale(self, today: date) -> bool:
        return (today - self.price_as_of).days > STALE_ESTIMATE_DAYS


@dataclass(frozen=True, slots=True)
class ScheduledPurchase:
    item: QueueItem
    cost: Money
    stale: bool


@dataclass(frozen=True, slots=True)
class MonthRecord:
    """The explanation for one month: what came in, what was bought, what is left."""

    month: date
    opening: Money
    contribution: Money
    overrides: tuple[MonthOverride, ...]
    purchases: tuple[ScheduledPurchase, ...]
    closing: Money
    blocked: bool
    """True when the balance stayed below zero after the contribution: nothing is bought."""

    @property
    def overrides_total(self) -> Money:
        return sum_money((o.delta for o in self.overrides), self.opening.currency)

    @property
    def purchases_total(self) -> Money:
        return sum_money((p.cost for p in self.purchases), self.opening.currency)

    @property
    def stale(self) -> bool:
        return any(p.stale for p in self.purchases)


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    item: QueueItem
    cost: Money
    status: ItemStatus
    month: date | None
    stale: bool


@dataclass(frozen=True, slots=True)
class Schedule:
    contingency_pct: int
    opening_balance: Money
    """Month-1 opening: earmarked funds less the reserve less purchases since ``balance_as_of``."""
    months: tuple[MonthRecord, ...]
    items: tuple[ItemOutcome, ...]
    """Every queued item in queue order, scheduled or not."""
    recovery_month: date | None
    """When a negative opening balance first came back to zero or more; None if it never did."""

    @property
    def blocked(self) -> bool:
        return self.opening_balance.amount < 0

    @property
    def scheduled(self) -> tuple[ItemOutcome, ...]:
        return tuple(i for i in self.items if i.status == "scheduled")

    @property
    def unscheduled(self) -> tuple[ItemOutcome, ...]:
        return tuple(i for i in self.items if i.status != "scheduled")

    @property
    def scheduled_total(self) -> Money:
        return sum_money((i.cost for i in self.scheduled), self.opening_balance.currency)

    @property
    def last_month(self) -> date | None:
        dates = [i.month for i in self.items if i.month is not None]
        return max(dates) if dates else None


@dataclass(frozen=True, slots=True)
class Forecast:
    today: date
    plan: BudgetPlan
    recent_purchases_total: Money
    optimistic: Schedule
    conservative: Schedule
    unpriced_count: int
    unpriced_ahead: bool
    """An unpriced row is at least as urgent as something scheduled: the timeline is optimistic."""

    @property
    def lower_bound(self) -> bool:
        return self.unpriced_count > 0

    @property
    def balance_stale(self) -> bool:
        return (self.today - self.plan.balance_as_of).days > STALE_BALANCE_DAYS

    @property
    def queue(self) -> tuple[QueueItem, ...]:
        return tuple(outcome.item for outcome in self.conservative.items)


def queue_from_backlog(backlog: Backlog) -> tuple[QueueItem, ...]:
    """Priced rows with net shortfall > 0, by row priority then workspace backlog order."""
    position = {item_id: index for index, item_id in enumerate(backlog.order)}
    rows = [row for row in backlog.rows if row.net_shortfall > 0 and row.unit_price is not None]
    rows.sort(key=lambda row: (row.priority, position[row.item.id]))
    return tuple(
        QueueItem(
            item_id=row.item.id,
            name=row.item.name,
            unit=row.item.unit,
            priority=row.priority,
            qty=row.net_shortfall,
            unit_price=row.unit_price,
            price_as_of=row.item.price_as_of or date.min,
        )
        for row in rows
        if row.unit_price is not None
    )


def schedule(
    plan: BudgetPlan,
    queue: Sequence[QueueItem],
    recent_purchases_total: Money,
    today: date,
    *,
    contingency_pct: int,
) -> Schedule:
    """Walk the months from this one, buying strictly from the head of the queue while it fits."""
    currency = plan.currency
    opening_balance = plan.earmarked_funds - plan.protected_reserve - recent_purchases_total
    balance = opening_balance
    remaining = list(queue)
    outcomes: dict[str, ItemOutcome] = {}
    months: list[MonthRecord] = []
    recovery: date | None = None
    start = month_start(today)
    horizon_end = add_months(start, plan.horizon_months)

    for index in range(plan.horizon_months):
        month = add_months(start, index)
        opening = balance
        overrides = tuple(o for o in plan.overrides if o.month == month)
        balance = (
            opening + plan.monthly_contribution + sum_money((o.delta for o in overrides), currency)
        )
        blocked = balance.amount < 0
        if opening_balance.amount < 0 and recovery is None and not blocked:
            recovery = month
        bought: list[ScheduledPurchase] = []
        if not blocked:
            while remaining and remaining[0].cost(contingency_pct) <= balance:
                item = remaining.pop(0)
                cost = item.cost(contingency_pct)
                balance = balance - cost
                stale = item.stale(today)
                bought.append(ScheduledPurchase(item=item, cost=cost, stale=stale))
                outcomes[item.item_id] = ItemOutcome(
                    item=item, cost=cost, status="scheduled", month=month, stale=stale
                )
        months.append(
            MonthRecord(
                month=month,
                opening=opening,
                contribution=plan.monthly_contribution,
                overrides=overrides,
                purchases=tuple(bought),
                closing=balance,
                blocked=blocked,
            )
        )
        if not remaining:
            break
        if not _more_money_coming(plan, month, horizon_end):
            for item in remaining:
                outcomes[item.item_id] = _unscheduled(item, "not_fundable", today, contingency_pct)
            remaining = []
            break

    for item in remaining:
        status: ItemStatus = (
            "blocked" if recovery is None and opening_balance.amount < 0 else "beyond_horizon"
        )
        outcomes[item.item_id] = _unscheduled(item, status, today, contingency_pct)

    return Schedule(
        contingency_pct=contingency_pct,
        opening_balance=opening_balance,
        months=tuple(months),
        items=tuple(outcomes[item.item_id] for item in queue),
        recovery_month=recovery,
    )


def _more_money_coming(plan: BudgetPlan, after: date, horizon_end: date) -> bool:
    """False when the contribution is zero or less and no positive override lies ahead."""
    if plan.monthly_contribution.amount > 0:
        return True
    return any(o.delta.amount > 0 and after < o.month < horizon_end for o in plan.overrides)


def _unscheduled(
    item: QueueItem, status: ItemStatus, today: date, contingency_pct: int
) -> ItemOutcome:
    return ItemOutcome(
        item=item,
        cost=item.cost(contingency_pct),
        status=status,
        month=None,
        stale=item.stale(today),
    )


def extend_through(sched: Schedule, plan: BudgetPlan, month_count: int) -> Schedule:
    """Carry the accounting on (contribution and overrides, nothing bought) to ``month_count`` months.

    A schedule stops recording once its queue is empty. When two schedules are shown
    side by side the shorter one must keep counting rather than repeat its last balance.
    """
    if len(sched.months) >= month_count:
        return sched
    months = list(sched.months)
    recovery = sched.recovery_month
    start = months[0].month
    for index in range(len(months), month_count):
        month = add_months(start, index)
        opening = months[-1].closing
        overrides = tuple(o for o in plan.overrides if o.month == month)
        closing = (
            opening
            + plan.monthly_contribution
            + sum_money((o.delta for o in overrides), plan.currency)
        )
        blocked = closing.amount < 0
        if sched.opening_balance.amount < 0 and recovery is None and not blocked:
            recovery = month
        months.append(
            MonthRecord(
                month=month,
                opening=opening,
                contribution=plan.monthly_contribution,
                overrides=overrides,
                purchases=(),
                closing=closing,
                blocked=blocked,
            )
        )
    return Schedule(
        contingency_pct=sched.contingency_pct,
        opening_balance=sched.opening_balance,
        months=tuple(months),
        items=sched.items,
        recovery_month=recovery,
    )


def forecast(
    plan: BudgetPlan,
    backlog: Backlog,
    recent_purchases_total: Money,
    today: date,
) -> Forecast:
    """Both schedules, over the same months, plus the honesty flags."""
    queue = queue_from_backlog(backlog)
    unpriced_ahead = bool(queue) and any(
        row.priority <= max(item.priority for item in queue) for row in backlog.unpriced
    )
    optimistic = schedule(plan, queue, recent_purchases_total, today, contingency_pct=0)
    conservative = schedule(
        plan, queue, recent_purchases_total, today, contingency_pct=plan.contingency_pct
    )
    span = max(len(optimistic.months), len(conservative.months))
    return Forecast(
        today=today,
        plan=plan,
        recent_purchases_total=recent_purchases_total,
        optimistic=extend_through(optimistic, plan, span),
        conservative=extend_through(conservative, plan, span),
        unpriced_count=backlog.unpriced_count,
        unpriced_ahead=unpriced_ahead,
    )
