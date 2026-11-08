"""The budget plan: one per workspace, balance-as-of semantics, overrides, validation."""

from datetime import date, datetime

import pytest

from projectbudget.domain.money import CurrencyMismatchError, Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import budget
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 8, 9, 0, 0)
TODAY = date(2026, 11, 8)


def usd(text: str) -> Money:
    return Money.parse(text, "USD")


def update(
    balance: str = "150.00", *, as_of: date | None = None, **kwargs: object
) -> budget.PlanUpdate:
    fields: dict[str, object] = {
        "earmarked_funds": usd(balance),
        "protected_reserve": usd("20.00"),
        "monthly_contribution": usd("25.00"),
        "balance_as_of": as_of,
    }
    fields.update(kwargs)
    return budget.PlanUpdate(**fields)  # type: ignore[arg-type]


def test_no_plan_until_one_is_set(session_factory: SessionFactory, principal: Principal) -> None:
    assert budget.get_plan(session_factory, principal) is None
    with pytest.raises(budget.PlanNotFoundError):
        budget.add_override(
            session_factory, principal, budget.OverrideCreate(TODAY, usd("10.00"), "Bonus")
        )


def test_set_plan_creates_then_replaces(
    session_factory: SessionFactory, principal: Principal
) -> None:
    created = budget.set_plan(session_factory, principal, update(), today=TODAY, now=NOW)
    assert created.currency == "USD"
    assert created.earmarked_funds == usd("150.00")
    assert created.balance_as_of == TODAY  # no date given: stamped today
    assert created.horizon_months == 36
    assert created.contingency_pct == 0
    assert created.overrides == ()
    assert budget.get_plan(session_factory, principal) == created

    replaced = budget.set_plan(
        session_factory,
        principal,
        update("150.00", horizon_months=12, contingency_pct=10),
        today=date(2026, 12, 1),
        now=NOW,
    )
    assert replaced.balance_as_of == TODAY  # same balance: the date is kept
    assert (replaced.horizon_months, replaced.contingency_pct) == (12, 10)


def test_editing_the_balance_resets_balance_as_of_unless_a_date_is_given(
    session_factory: SessionFactory, principal: Principal
) -> None:
    budget.set_plan(session_factory, principal, update("150.00", as_of=date(2026, 10, 1)))
    changed = budget.set_plan(session_factory, principal, update("200.00"), today=TODAY)
    assert changed.balance_as_of == TODAY
    explicit = budget.set_plan(
        session_factory, principal, update("210.00", as_of=date(2026, 11, 1)), today=TODAY
    )
    assert explicit.balance_as_of == date(2026, 11, 1)


def test_plan_validation(session_factory: SessionFactory, principal: Principal) -> None:
    with pytest.raises(budget.InvalidPlanError):
        budget.set_plan(session_factory, principal, update(horizon_months=0))
    with pytest.raises(budget.InvalidPlanError):
        budget.set_plan(session_factory, principal, update(horizon_months=121))
    with pytest.raises(budget.InvalidPlanError):
        budget.set_plan(session_factory, principal, update(contingency_pct=101))
    with pytest.raises(budget.InvalidPlanError):
        budget.set_plan(session_factory, principal, update(protected_reserve=usd("-1.00")))
    with pytest.raises(CurrencyMismatchError):
        budget.set_plan(session_factory, principal, update(monthly_contribution=Money(100, "EUR")))
    assert budget.get_plan(session_factory, principal) is None


def test_overrides_are_normalised_to_the_month_and_removable(
    session_factory: SessionFactory, principal: Principal
) -> None:
    budget.set_plan(session_factory, principal, update())
    bonus = budget.add_override(
        session_factory,
        principal,
        budget.OverrideCreate(date(2026, 12, 25), usd("200.00"), "  Year-end   bonus "),
        now=NOW,
    )
    assert bonus.month == date(2026, 12, 1)
    assert bonus.label == "Year-end bonus"
    bill = budget.add_override(
        session_factory, principal, budget.OverrideCreate(date(2026, 11, 3), usd("-50.00"), "Tax")
    )
    plan = budget.get_plan(session_factory, principal)
    assert plan is not None
    assert [o.id for o in plan.overrides] == [bill.id, bonus.id]  # by month

    budget.remove_override(session_factory, principal, bill.id)
    plan = budget.get_plan(session_factory, principal)
    assert plan is not None
    assert [o.id for o in plan.overrides] == [bonus.id]
    with pytest.raises(budget.OverrideNotFoundError):
        budget.remove_override(session_factory, principal, bill.id)

    with pytest.raises(budget.InvalidOverrideError):
        budget.add_override(session_factory, principal, budget.OverrideCreate(TODAY, usd("0"), "x"))
    with pytest.raises(budget.InvalidOverrideError):
        budget.add_override(
            session_factory, principal, budget.OverrideCreate(TODAY, usd("1.00"), "   ")
        )
    with pytest.raises(CurrencyMismatchError):
        budget.add_override(
            session_factory, principal, budget.OverrideCreate(TODAY, Money(100, "EUR"), "x")
        )


def test_to_domain_carries_every_field(
    session_factory: SessionFactory, principal: Principal
) -> None:
    budget.set_plan(session_factory, principal, update(horizon_months=6, contingency_pct=5))
    budget.add_override(session_factory, principal, budget.OverrideCreate(TODAY, usd("9.00"), "x"))
    info = budget.get_plan(session_factory, principal)
    assert info is not None
    plan = budget.to_domain(info)
    assert plan.horizon_months == 6
    assert plan.contingency_pct == 5
    assert plan.overrides[0].delta == usd("9.00")
    assert plan.overrides[0].month == date(2026, 11, 1)


def test_read_only_principals_cannot_change_the_plan(
    session_factory: SessionFactory, principal: Principal, read_only_principal: Principal
) -> None:
    budget.set_plan(session_factory, principal, update())
    with pytest.raises(ReadOnlyPrincipalError):
        budget.set_plan(session_factory, read_only_principal, update("1.00"))
    with pytest.raises(ReadOnlyPrincipalError):
        budget.add_override(
            session_factory, read_only_principal, budget.OverrideCreate(TODAY, usd("1.00"), "x")
        )
    assert budget.get_plan(session_factory, read_only_principal) is not None
