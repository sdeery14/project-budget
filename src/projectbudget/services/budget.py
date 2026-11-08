"""The budget plan and its month overrides (ADR-006). Exactly one plan per workspace."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from projectbudget.domain import forecast as domain
from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import BudgetPlan, MonthOverride
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.clock import today as clock_today
from projectbudget.services.clock import utcnow
from projectbudget.services.guards import NotFoundError, require_write

PLAN_ID = "workspace"
LABEL_MAX_LENGTH = 100


class PlanNotFoundError(NotFoundError):
    entity = "Budget plan"


class OverrideNotFoundError(NotFoundError):
    entity = "Month override"


class InvalidPlanError(ValueError):
    """Horizon or contingency out of range, or a negative reserve."""


class InvalidOverrideError(ValueError):
    """Zero delta or a bad label."""


@dataclass(frozen=True, slots=True)
class OverrideInfo:
    id: str
    month: date
    delta: Money
    label: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PlanInfo:
    currency: str
    earmarked_funds: Money
    balance_as_of: date
    protected_reserve: Money
    monthly_contribution: Money
    horizon_months: int
    contingency_pct: int
    updated_at: datetime
    overrides: tuple[OverrideInfo, ...]


@dataclass(frozen=True, slots=True)
class PlanUpdate:
    """The whole plan. ``balance_as_of`` left as None means today when the balance changed."""

    earmarked_funds: Money
    protected_reserve: Money
    monthly_contribution: Money
    horizon_months: int = domain.DEFAULT_HORIZON_MONTHS
    contingency_pct: int = 0
    balance_as_of: date | None = None


@dataclass(frozen=True, slots=True)
class OverrideCreate:
    month: date
    delta: Money
    label: str


def _override_info(row: MonthOverride, currency: str) -> OverrideInfo:
    return OverrideInfo(
        id=row.id,
        month=row.month,
        delta=Money(row.delta, currency),
        label=row.label,
        created_at=row.created_at,
    )


def _info(row: BudgetPlan, overrides: list[MonthOverride]) -> PlanInfo:
    return PlanInfo(
        currency=row.currency,
        earmarked_funds=Money(row.earmarked_funds, row.currency),
        balance_as_of=row.balance_as_of,
        protected_reserve=Money(row.protected_reserve, row.currency),
        monthly_contribution=Money(row.monthly_contribution, row.currency),
        horizon_months=row.horizon_months,
        contingency_pct=row.contingency_pct,
        updated_at=row.updated_at,
        overrides=tuple(_override_info(o, row.currency) for o in overrides),
    )


def _load_overrides(db: Session) -> list[MonthOverride]:
    return list(
        db.scalars(
            select(MonthOverride)
            .where(MonthOverride.plan_id == PLAN_ID)
            .order_by(MonthOverride.month, MonthOverride.created_at, MonthOverride.id)
        ).all()
    )


def _check_currency(currency: str, *amounts: Money) -> None:
    for amount in amounts:
        if amount.currency != currency:
            raise CurrencyMismatchError(currency, amount.currency)


def get_plan(session_factory: SessionFactory, principal: Principal) -> PlanInfo | None:
    with session_factory() as db:
        row = db.get(BudgetPlan, PLAN_ID)
        if row is None:
            return None
        return _info(row, _load_overrides(db))


def set_plan(
    session_factory: SessionFactory,
    principal: Principal,
    command: PlanUpdate,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> PlanInfo:
    """Create or replace the plan. Changing the balance without a date resets it to today."""
    require_write(principal)
    currency = workspace.get_currency(session_factory)
    _check_currency(
        currency, command.earmarked_funds, command.protected_reserve, command.monthly_contribution
    )
    if not 1 <= command.horizon_months <= domain.MAX_HORIZON_MONTHS:
        raise InvalidPlanError(f"Horizon must be 1-{domain.MAX_HORIZON_MONTHS} months")
    if not 0 <= command.contingency_pct <= 100:
        raise InvalidPlanError("Contingency must be 0-100 percent")
    if command.protected_reserve.is_negative:
        raise InvalidPlanError("The protected reserve cannot be negative")
    moment = now or utcnow()
    day = today or clock_today()
    with session_factory.begin() as db:
        row = db.get(BudgetPlan, PLAN_ID)
        if row is None:
            row = BudgetPlan(
                id=PLAN_ID,
                currency=currency,
                earmarked_funds=command.earmarked_funds.amount,
                balance_as_of=command.balance_as_of or day,
                protected_reserve=command.protected_reserve.amount,
                monthly_contribution=command.monthly_contribution.amount,
                horizon_months=command.horizon_months,
                contingency_pct=command.contingency_pct,
                updated_at=moment,
            )
            db.add(row)
        else:
            if command.balance_as_of is not None:
                row.balance_as_of = command.balance_as_of
            elif command.earmarked_funds.amount != row.earmarked_funds:
                row.balance_as_of = day
            row.currency = currency
            row.earmarked_funds = command.earmarked_funds.amount
            row.protected_reserve = command.protected_reserve.amount
            row.monthly_contribution = command.monthly_contribution.amount
            row.horizon_months = command.horizon_months
            row.contingency_pct = command.contingency_pct
            row.updated_at = moment
        db.flush()
        return _info(row, _load_overrides(db))


def add_override(
    session_factory: SessionFactory,
    principal: Principal,
    command: OverrideCreate,
    *,
    now: datetime | None = None,
) -> OverrideInfo:
    require_write(principal)
    label = " ".join(command.label.split())
    if not label or len(label) > LABEL_MAX_LENGTH:
        raise InvalidOverrideError(f"Label must be 1-{LABEL_MAX_LENGTH} characters")
    if command.delta.amount == 0:
        raise InvalidOverrideError("An override changes the month by a non-zero amount")
    with session_factory.begin() as db:
        plan = db.get(BudgetPlan, PLAN_ID)
        if plan is None:
            raise PlanNotFoundError(PLAN_ID)
        _check_currency(plan.currency, command.delta)
        row = MonthOverride(
            id=str(uuid.uuid4()),
            plan_id=PLAN_ID,
            month=domain.month_start(command.month),
            delta=command.delta.amount,
            label=label,
            created_at=now or utcnow(),
        )
        db.add(row)
        db.flush()
        return _override_info(row, plan.currency)


def remove_override(
    session_factory: SessionFactory, principal: Principal, override_id: str
) -> None:
    require_write(principal)
    with session_factory.begin() as db:
        row = db.get(MonthOverride, override_id)
        if row is None:
            raise OverrideNotFoundError(override_id)
        db.delete(row)


def to_domain(info: PlanInfo) -> domain.BudgetPlan:
    return domain.BudgetPlan(
        currency=info.currency,
        earmarked_funds=info.earmarked_funds,
        balance_as_of=info.balance_as_of,
        protected_reserve=info.protected_reserve,
        monthly_contribution=info.monthly_contribution,
        horizon_months=info.horizon_months,
        contingency_pct=info.contingency_pct,
        overrides=tuple(
            domain.MonthOverride(month=o.month, delta=o.delta, label=o.label)
            for o in info.overrides
        ),
    )
