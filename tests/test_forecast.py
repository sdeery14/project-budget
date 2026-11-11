"""Golden tests for the forecast engine: every rule and guard in RFC v3 §5 (ADR-006)."""

from datetime import date

from projectbudget.domain.catalog import Intent, ItemKind, Sharing
from projectbudget.domain.consolidation import Backlog, ItemFacts, RequirementLine, consolidate
from projectbudget.domain.forecast import (
    BudgetPlan,
    MonthOverride,
    QueueItem,
    add_months,
    extend_through,
    forecast,
    month_start,
    queue_from_backlog,
    schedule,
)
from projectbudget.domain.money import Money

USD = "USD"
TODAY = date(2026, 11, 8)
NOV, DEC, JAN, FEB = date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)


def usd(text: str) -> Money:
    return Money.parse(text, USD)


def plan(
    balance: str = "100.00",
    *,
    reserve: str = "0.00",
    contribution: str = "50.00",
    horizon: int = 36,
    contingency: int = 0,
    as_of: date = date(2026, 11, 1),
    overrides: tuple[MonthOverride, ...] = (),
) -> BudgetPlan:
    return BudgetPlan(
        currency=USD,
        earmarked_funds=usd(balance),
        balance_as_of=as_of,
        protected_reserve=usd(reserve),
        monthly_contribution=usd(contribution),
        horizon_months=horizon,
        contingency_pct=contingency,
        overrides=overrides,
    )


def backlog(
    *rows: tuple[str, int, str | None],
    priorities: dict[str, int] | None = None,
    order: list[str] | None = None,
    on_order: dict[str, int] | None = None,
    price_as_of: date = date(2026, 11, 1),
) -> Backlog:
    """Rows as (item id, quantity needed, unit price or None); on-hand is zero."""
    items = [
        ItemFacts(
            id=item_id,
            slug=item_id,
            name=item_id.capitalize(),
            kind=ItemKind.CONSUMABLE,
            unit="each",
            on_order=(on_order or {}).get(item_id, 0),
            unit_price=usd(price) if price is not None else None,
            price_as_of=price_as_of if price is not None else None,
        )
        for item_id, _qty, price in rows
    ]
    lines = [
        RequirementLine(
            id=f"r-{item_id}",
            project_id="p",
            catalog_item_id=item_id,
            qty=qty,
            priority=(priorities or {}).get(item_id, 2),
            sharing=Sharing.SHARED,
            intent=Intent.ACTIVE,
        )
        for item_id, qty, _price in rows
    ]
    return consolidate(items, lines, order=order or [], currency=USD)


def test_month_arithmetic() -> None:
    assert month_start(TODAY) == NOV
    assert add_months(NOV, 1) == DEC
    assert add_months(NOV, 2) == JAN
    assert add_months(NOV, 14) == date(2028, 1, 1)
    assert add_months(JAN, -1) == DEC


def test_month_one_opening_subtracts_reserve_and_purchases_since_balance_as_of() -> None:
    result = forecast(plan("500.00", reserve="100.00"), backlog(), usd("75.00"), TODAY)
    assert result.conservative.opening_balance == usd("325.00")
    assert result.conservative.months[0].opening == usd("325.00")
    # Spend before balance_as_of is already reflected in the balance and is not passed in.
    untouched = forecast(plan("500.00", reserve="100.00"), backlog(), usd("0.00"), TODAY)
    assert untouched.conservative.opening_balance == usd("400.00")


def test_month_one_gets_the_full_contribution_and_buys_in_strict_order() -> None:
    rows = backlog(("saw", 1, "60.00"), ("glue", 1, "5.00"), order=["saw", "glue"])
    result = forecast(plan("0.00", contribution="50.00"), rows, usd("0"), TODAY)
    months = result.conservative.months
    # Month 1: 0 + 50 = 50 < 60: the saw does not fit and the cheaper glue is NOT skipped ahead.
    assert months[0].purchases == ()
    assert months[0].closing == usd("50.00")
    # Month 2: 100 buys the saw (40 left) and then the glue (35 left).
    assert [p.item.item_id for p in months[1].purchases] == ["saw", "glue"]
    assert months[1].closing == usd("35.00")
    assert [i.month for i in result.conservative.items] == [DEC, DEC]
    assert len(months) == 2  # the record stops when the queue is empty


def test_overrides_apply_after_the_contribution_and_are_labeled() -> None:
    bonus = MonthOverride(month=DEC, delta=usd("200.00"), label="Bonus")
    car = MonthOverride(month=DEC, delta=usd("-50.00"), label="Car insurance")
    rows = backlog(("saw", 1, "180.00"))
    result = forecast(
        plan("0.00", contribution="10.00", overrides=(bonus, car)), rows, usd("0"), TODAY
    )
    months = result.conservative.months
    assert months[0].overrides == ()
    assert months[1].overrides == (bonus, car)
    assert months[1].overrides_total == usd("150.00")
    assert months[1].opening == usd("10.00")
    # 10 + 10 + 200 - 50 = 170 < 180: still not enough in December.
    assert months[1].purchases == ()
    assert months[1].closing == usd("170.00")
    assert months[2].purchases[0].item.item_id == "saw"


def test_purchase_unit_is_net_shortfall_with_contingency_rounded_up() -> None:
    rows = backlog(("bolt", 10, "0.45"), ("ordered", 5, "1.00"), on_order={"bolt": 3, "ordered": 5})
    queue = queue_from_backlog(rows)
    # The fully ordered row has net shortfall 0 and does not appear in the timeline.
    assert [q.item_id for q in queue] == ["bolt"]
    assert queue[0].qty == 7
    assert queue[0].cost(0) == usd("3.15")
    assert queue[0].cost(10) == Money(347, USD)  # 315 * 1.1 = 346.5, rounded up
    assert queue[0].cost(100) == usd("6.30")


def test_queue_order_is_priority_then_workspace_backlog_order() -> None:
    rows = backlog(
        ("a", 1, "1.00"),
        ("b", 1, "1.00"),
        ("c", 1, "1.00"),
        priorities={"a": 3, "b": 1, "c": 1},
        order=["a", "c", "b"],
    )
    assert [q.item_id for q in queue_from_backlog(rows)] == ["c", "b", "a"]


def test_not_fundable_at_current_inputs() -> None:
    rows = backlog(("saw", 1, "60.00"), ("glue", 1, "5.00"), order=["saw", "glue"])
    result = forecast(plan("50.00", contribution="0.00"), rows, usd("0"), TODAY)
    sched = result.conservative
    assert [i.status for i in sched.items] == ["not_fundable", "not_fundable"]
    assert [i.month for i in sched.items] == [None, None]
    assert len(sched.months) == 1
    # A positive override later in the horizon means money is still coming: wait for it instead.
    bonus = MonthOverride(month=JAN, delta=usd("20.00"), label="Bonus")
    later = forecast(plan("50.00", contribution="0.00", overrides=(bonus,)), rows, usd("0"), TODAY)
    assert [i.status for i in later.conservative.items] == ["scheduled", "scheduled"]
    assert [i.month for i in later.conservative.items] == [JAN, JAN]


def test_beyond_horizon() -> None:
    rows = backlog(("saw", 1, "60.00"), ("drill", 1, "500.00"), order=["saw", "drill"])
    result = forecast(plan("0.00", contribution="50.00", horizon=3), rows, usd("0"), TODAY)
    saw, drill = result.conservative.items
    assert (saw.status, saw.month) == ("scheduled", DEC)
    assert (drill.status, drill.month) == ("beyond_horizon", None)
    assert len(result.conservative.months) == 3
    assert result.conservative.last_month == DEC


def test_stale_estimate_flags_the_item_and_its_month() -> None:
    old = backlog(("saw", 1, "10.00"), price_as_of=date(2026, 8, 1))  # 99 days before TODAY
    fresh = backlog(("saw", 1, "10.00"), price_as_of=date(2026, 8, 10))  # exactly 90 days
    stale = forecast(plan(), old, usd("0"), TODAY).conservative
    assert stale.items[0].stale is True
    assert stale.months[0].stale is True
    assert stale.months[0].purchases[0].stale is True
    ok = forecast(plan(), fresh, usd("0"), TODAY).conservative
    assert ok.items[0].stale is False
    assert ok.months[0].stale is False


def test_blocked_until_the_balance_recovers() -> None:
    rows = backlog(("saw", 1, "10.00"))
    # 100 - 50 reserve - 180 recent spend = -130; +50 a month: Nov -80, Dec -30, Jan +20 buys the saw.
    result = forecast(
        plan("100.00", reserve="50.00", contribution="50.00"), rows, usd("180.00"), TODAY
    )
    sched = result.conservative
    assert sched.blocked
    assert sched.opening_balance == usd("-130.00")
    assert [m.blocked for m in sched.months] == [True, True, False]
    assert sched.months[0].purchases == ()
    assert sched.months[1].purchases == ()
    assert sched.recovery_month == JAN
    assert sched.items[0].month == JAN

    never = forecast(
        plan("100.00", reserve="50.00", contribution="0.00"), rows, usd("180.00"), TODAY
    )
    assert never.conservative.recovery_month is None
    assert never.conservative.items[0].status == "not_fundable"  # nothing is coming in
    stuck = forecast(
        plan("100.00", reserve="50.00", contribution="1.00", horizon=6), rows, usd("180.00"), TODAY
    )
    assert stuck.conservative.recovery_month is None
    assert stuck.conservative.items[0].status == "blocked"


def test_a_reserve_the_contribution_covers_is_not_a_debt_and_not_blocked() -> None:
    """#21: balance 0, reserve 100, contribution 350: month 1 buys; spendable is 0, not -100."""
    rows = backlog(("pump", 1, "39.00"))
    result = forecast(plan("0.00", reserve="100.00", contribution="350.00"), rows, usd("0"), TODAY)
    sched = result.conservative
    assert sched.opening_balance == usd("-100.00")  # the arithmetic is unchanged
    assert sched.spendable == usd("0.00")
    assert sched.reserve_shortfall == usd("100.00")
    assert not sched.blocked
    assert sched.recovery_month is None  # nothing to recover from
    assert sched.months[0].blocked is False
    assert [p.item.item_id for p in sched.months[0].purchases] == ["pump"]
    assert sched.months[0].closing == usd("211.00")
    assert sched.items[0].month == NOV

    covered = forecast(plan("250.00", reserve="100.00"), backlog(), usd("0"), TODAY)
    assert covered.conservative.spendable == usd("150.00")
    assert covered.conservative.reserve_shortfall == usd("0.00")


def test_balance_may_be_stale_after_sixty_days() -> None:
    fresh = forecast(plan(as_of=date(2026, 9, 9)), backlog(), usd("0"), TODAY)
    assert fresh.balance_stale is False  # exactly 60 days
    stale = forecast(plan(as_of=date(2026, 9, 8)), backlog(), usd("0"), TODAY)
    assert stale.balance_stale is True


def test_unpriced_items_are_counted_never_summed_and_flag_optimism() -> None:
    rows = backlog(("saw", 1, "10.00"), ("mystery", 1, None), priorities={"saw": 2, "mystery": 1})
    result = forecast(plan(), rows, usd("0"), TODAY)
    assert result.unpriced_count == 1
    assert result.lower_bound
    assert result.unpriced_ahead  # the unpriced P1 would be bought before the P2 saw
    assert [q.item_id for q in result.queue] == ["saw"]
    assert result.conservative.scheduled_total == usd("10.00")

    behind = backlog(("saw", 1, "10.00"), ("mystery", 1, None), priorities={"saw": 1, "mystery": 3})
    assert forecast(plan(), behind, usd("0"), TODAY).unpriced_ahead is False
    assert forecast(plan(), backlog(("mystery", 1, None)), usd("0"), TODAY).unpriced_ahead is False


def test_contingency_yields_optimistic_and_conservative_dates() -> None:
    rows = backlog(("saw", 1, "95.00"))
    result = forecast(plan("0.00", contribution="50.00", contingency=10), rows, usd("0"), TODAY)
    assert result.optimistic.contingency_pct == 0
    assert result.conservative.contingency_pct == 10
    assert result.optimistic.items[0].cost == usd("95.00")
    assert result.conservative.items[0].cost == usd("104.50")
    assert result.optimistic.items[0].month == DEC  # 100 >= 95
    assert result.conservative.items[0].month == JAN  # 150 >= 104.50


def test_forecast_is_deterministic_and_touches_only_its_inputs() -> None:
    rows = backlog(("a", 2, "3.00"), ("b", 1, "40.00"), ("c", 1, None))
    first = forecast(plan("10.00", contribution="20.00", contingency=5), rows, usd("2.00"), TODAY)
    second = forecast(plan("10.00", contribution="20.00", contingency=5), rows, usd("2.00"), TODAY)
    assert first == second


def test_queue_item_cost_is_exact_money() -> None:
    item = QueueItem("x", "X", "each", 2, 3, usd("0.01"), date(2026, 11, 1))
    assert item.cost(0) == Money(3, USD)
    assert item.cost(33) == Money(4, USD)  # 3.99 rounds up to 4 minor units


def test_both_schedules_cover_the_same_months_without_repeating_a_balance() -> None:
    """A 50 contribution and a 95 purchase in December: January must show 55, never a copied 5."""
    rows = backlog(("saw", 1, "95.00"))
    result = forecast(plan("0.00", contribution="50.00", contingency=10), rows, usd("0"), TODAY)
    optimistic, conservative = result.optimistic, result.conservative
    assert [m.month for m in optimistic.months] == [NOV, DEC, JAN]
    assert [m.month for m in conservative.months] == [NOV, DEC, JAN]
    assert [str(m.closing) for m in optimistic.months] == ["50.00", "5.00", "55.00"]
    assert [str(m.closing) for m in conservative.months] == ["50.00", "100.00", "45.50"]
    carried = optimistic.months[2]
    assert carried.purchases == ()
    assert carried.opening == usd("5.00")
    assert carried.contribution == usd("50.00")
    assert [i.month for i in optimistic.items] == [DEC]  # items are untouched by the extension


def test_extension_applies_overrides_and_tracks_recovery() -> None:
    bonus = MonthOverride(month=JAN, delta=usd("100.00"), label="Bonus")
    base = plan("0.00", contribution="10.00", overrides=(bonus,))
    short = schedule(base, (), usd("30.00"), TODAY, contingency_pct=0)  # opening -30, one month
    assert [str(m.closing) for m in short.months] == ["-20.00"]
    assert short.recovery_month is None
    longer = extend_through(short, base, 3)
    assert [str(m.closing) for m in longer.months] == ["-20.00", "-10.00", "100.00"]
    assert [m.blocked for m in longer.months] == [True, True, False]
    assert longer.months[2].overrides == (bonus,)
    assert longer.recovery_month == JAN
    assert extend_through(longer, base, 2) is longer
