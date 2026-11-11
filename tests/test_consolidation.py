"""Consolidation rules as named tests, including the RFC worked example (ADR-004)."""

from datetime import date

import pytest

from projectbudget.domain.catalog import Intent, ItemKind, Sharing
from projectbudget.domain.consolidation import (
    ItemFacts,
    RequirementLine,
    consolidate,
    gross_shortfall,
    needed_quantity,
    row_priority,
)
from projectbudget.domain.money import Money

USD = "USD"


def line(
    item: str,
    qty: int,
    *,
    project: str = "A",
    priority: int = 2,
    sharing: Sharing = Sharing.SHARED,
    intent: Intent = Intent.ACTIVE,
    consumed: int = 0,
    id_: str | None = None,
    sort_order: int = 0,
) -> RequirementLine:
    return RequirementLine(
        id=id_ or f"{project}-{item}-{qty}-{sharing.value}",
        project_id=project,
        catalog_item_id=item,
        qty=qty,
        priority=priority,
        sharing=sharing,
        intent=intent,
        consumed=consumed,
        sort_order=sort_order,
    )


def item(
    id_: str,
    kind: ItemKind,
    *,
    on_hand: int = 0,
    on_order: int = 0,
    price: str | None = None,
    name: str | None = None,
) -> ItemFacts:
    return ItemFacts(
        id=id_,
        slug=id_,
        name=name or id_,
        kind=kind,
        unit="each",
        on_hand=on_hand,
        on_order=on_order,
        unit_price=Money.parse(price, USD) if price is not None else None,
        price_as_of=date(2026, 11, 1) if price is not None else None,
    )


def test_worked_example_from_the_rfc_clamps() -> None:
    """A needs 3 shared, B needs 2 shared, C needs 1 exclusive; 1 owned -> shortfall 3."""
    lines = [
        line("clamp", 3, project="A"),
        line("clamp", 2, project="B"),
        line("clamp", 1, project="C", sharing=Sharing.EXCLUSIVE, id_="c-clamp"),
    ]
    assert needed_quantity(ItemKind.REUSABLE, lines) == 4
    assert gross_shortfall(ItemKind.REUSABLE, lines, on_hand=1) == 3
    # C's clamp is installed: consumed against C's requirement, on-hand now 0.
    installed = [*lines[:2], line("clamp", 1, project="C", sharing=Sharing.EXCLUSIVE, consumed=1)]
    assert needed_quantity(ItemKind.REUSABLE, installed) == 3
    assert gross_shortfall(ItemKind.REUSABLE, installed, on_hand=0) == 3


def test_consumables_add_up_and_subtract_consumption_and_stock() -> None:
    lines = [line("bolt", 10, project="A"), line("bolt", 5, project="B", consumed=2)]
    assert needed_quantity(ItemKind.CONSUMABLE, lines) == 13
    assert gross_shortfall(ItemKind.CONSUMABLE, lines, on_hand=4) == 9


def test_shared_reusable_takes_the_maximum_not_the_sum() -> None:
    lines = [line("saw", 1, project="A"), line("saw", 1, project="B"), line("saw", 2, project="C")]
    assert needed_quantity(ItemKind.REUSABLE, lines) == 2
    assert gross_shortfall(ItemKind.REUSABLE, lines, on_hand=2) == 0


def test_exclusive_reusable_requirements_each_add() -> None:
    lines = [
        line("vise", 1, project="A", sharing=Sharing.EXCLUSIVE),
        line("vise", 1, project="B", sharing=Sharing.EXCLUSIVE),
        line("vise", 1, project="C"),
    ]
    assert needed_quantity(ItemKind.REUSABLE, lines) == 3


def test_gross_shortfall_floors_at_zero_and_rows_at_zero_leave_the_backlog() -> None:
    backlog = consolidate(
        [item("bolt", ItemKind.CONSUMABLE, on_hand=50, price="0.10")],
        [line("bolt", 10)],
        order=[],
        currency=USD,
    )
    assert gross_shortfall(ItemKind.CONSUMABLE, [line("bolt", 10)], on_hand=50) == 0
    assert backlog.rows == ()
    assert backlog.unpriced == ()
    assert backlog.total == Money.zero(USD)


def test_only_active_requirements_contribute() -> None:
    lines = [
        line("bolt", 10, intent=Intent.ACTIVE),
        line("bolt", 7, intent=Intent.PAUSED),
        line("bolt", 7, intent=Intent.CANCELLED, consumed=3),
        line("bolt", 7, intent=Intent.ARCHIVED),
    ]
    assert needed_quantity(ItemKind.CONSUMABLE, lines) == 10
    backlog = consolidate(
        [item("bolt", ItemKind.CONSUMABLE, price="1.00")], lines, order=[], currency=USD
    )
    assert backlog.rows[0].requirement_ids == (lines[0].id,)


def test_row_priority_is_the_most_urgent_contributing_requirement() -> None:
    lines = [line("bolt", 1, priority=3), line("bolt", 1, project="B", priority=1)]
    assert row_priority(lines) == 1
    paused_urgent = [line("bolt", 1, priority=3), line("bolt", 1, priority=1, intent=Intent.PAUSED)]
    assert row_priority(paused_urgent) == 3


def test_on_order_is_a_separate_column_and_nets_only_the_forecast_quantity() -> None:
    backlog = consolidate(
        [item("bolt", ItemKind.CONSUMABLE, on_order=6, price="0.50")],
        [line("bolt", 10)],
        order=[],
        currency=USD,
    )
    (row,) = backlog.rows
    assert row.gross_shortfall == 10
    assert row.on_order == 6
    assert row.net_shortfall == 4
    # The backlog total is gross shortfall at the current price (ADR-004).
    assert row.line_total == Money(500, USD)
    assert backlog.total == Money(500, USD)
    fully_ordered = consolidate(
        [item("bolt", ItemKind.CONSUMABLE, on_order=12, price="0.50")],
        [line("bolt", 10)],
        order=[],
        currency=USD,
    )
    assert fully_ordered.rows[0].net_shortfall == 0  # stays visible until received


def test_unpriced_items_go_to_their_own_bucket_and_never_sum_as_zero() -> None:
    backlog = consolidate(
        [
            item("bolt", ItemKind.CONSUMABLE, price="0.25"),
            item("hinge", ItemKind.CONSUMABLE),
        ],
        [line("bolt", 4), line("hinge", 1, priority=1)],
        order=[],
        currency=USD,
    )
    assert [row.item.id for row in backlog.rows] == ["bolt"]
    assert [row.item.id for row in backlog.unpriced] == ["hinge"]
    assert backlog.unpriced[0].line_total is None
    assert backlog.total == Money(100, USD)
    assert backlog.is_lower_bound
    assert backlog.unpriced_count == 1


def test_workspace_order_wins_and_new_rows_append_by_priority_then_name() -> None:
    items = [
        item("a", ItemKind.CONSUMABLE, price="1.00", name="Zeta"),
        item("b", ItemKind.CONSUMABLE, price="1.00", name="Alpha"),
        item("c", ItemKind.CONSUMABLE, price="1.00", name="Mid"),
        item("d", ItemKind.CONSUMABLE, price="1.00", name="Urgent"),
    ]
    lines = [
        line("a", 1, priority=2),
        line("b", 1, priority=2),
        line("c", 1, priority=3),
        line("d", 1, priority=1),
    ]
    ordered = consolidate(items, lines, order=["c", "a"], currency=USD)
    assert [row.item.id for row in ordered.rows] == ["c", "a", "d", "b"]
    assert ordered.order == ("c", "a", "d", "b")
    unordered = consolidate(items, lines, order=[], currency=USD)
    assert [row.item.id for row in unordered.rows] == ["d", "b", "a", "c"]


def test_default_order_is_priority_then_project_then_requirement_order_then_name() -> None:
    """ADR-012 (#34): a manifest's file order survives into the queue; projects do not interleave."""
    items = [
        item(id_, ItemKind.CONSUMABLE, price="1.00", name=name)
        for id_, name in (
            ("pi", "Raspberry Pi"),
            ("cooler", "Active cooler"),
            ("psu", "Power supply"),
            ("pump", "Shock pump"),
            ("sealant", "Tyre sealant"),
            ("rope", "Rope"),
        )
    ]
    lines = [
        # Home system lists the Pi first, then what attaches to it; alphabetically it comes last.
        line("pi", 1, project="home", priority=1, sort_order=0),
        line("cooler", 1, project="home", priority=1, sort_order=1),
        line("psu", 1, project="home", priority=1, sort_order=2),
        # Garage: a P1 item listed after a P2 item still goes first.
        line("sealant", 1, project="garage", priority=2, sort_order=0),
        line("pump", 1, project="garage", priority=1, sort_order=1),
        # A project outside the project order ranks after the known ones; names break ties.
        line("rope", 1, project="shed", priority=1, sort_order=0),
    ]
    result = consolidate(items, lines, order=[], currency=USD, project_order=["garage", "home"])
    assert [row.item.id for row in result.rows] == [
        "pump",
        "pi",
        "cooler",
        "psu",
        "rope",
        "sealant",
    ]
    # Without a project order every project ties, so requirement order and then name decide.
    no_projects = consolidate(items, lines, order=[], currency=USD)
    assert [row.item.id for row in no_projects.rows] == [
        "pi",
        "rope",
        "cooler",
        "pump",
        "psu",
        "sealant",
    ]


def test_a_row_with_lines_from_two_projects_ranks_by_its_most_urgent_line() -> None:
    items = [
        item("a", ItemKind.CONSUMABLE, price="1.00", name="A"),
        item("b", ItemKind.CONSUMABLE, price="1.00", name="B"),
    ]
    lines = [
        line("a", 1, project="late", priority=1, sort_order=5),
        line("a", 1, project="early", priority=2, sort_order=0),
        line("b", 1, project="early", priority=1, sort_order=0),
    ]
    result = consolidate(items, lines, order=[], currency=USD, project_order=["early", "late"])
    # Both rows are P1; "b" is P1 in the earlier project, "a" is P1 only in the later one.
    assert [row.item.id for row in result.rows] == ["b", "a"]
    assert [row.priority for row in result.rows] == [1, 1]


def test_shared_flag_marks_rows_with_a_shared_reusable_contribution() -> None:
    items = [
        item("clamp", ItemKind.REUSABLE, price="12.50"),
        item("vise", ItemKind.REUSABLE, price="89.00"),
        item("bolt", ItemKind.CONSUMABLE, price="0.45"),
    ]
    lines = [
        line("clamp", 3),
        line("vise", 1, sharing=Sharing.EXCLUSIVE),
        line("bolt", 10, sharing=Sharing.SHARED),  # sharing is ignored for consumables
    ]
    flags = {
        row.item.id: row.shared for row in consolidate(items, lines, order=[], currency=USD).rows
    }
    assert flags == {"clamp": True, "vise": False, "bolt": False}


def test_rows_list_every_contributing_requirement_and_project() -> None:
    backlog = consolidate(
        [item("clamp", ItemKind.REUSABLE, on_hand=1, price="12.50")],
        [line("clamp", 3, project="A", id_="r1"), line("clamp", 2, project="B", id_="r2")],
        order=[],
        currency=USD,
    )
    (row,) = backlog.rows
    assert row.needed == 3
    assert row.gross_shortfall == 2
    assert row.requirement_ids == ("r1", "r2")
    assert row.project_ids == ("A", "B")
    assert row.line_total == Money(2500, USD)


def test_row_priority_needs_at_least_one_active_line() -> None:
    with pytest.raises(ValueError, match="empty"):
        row_priority([line("bolt", 1, intent=Intent.PAUSED)])
