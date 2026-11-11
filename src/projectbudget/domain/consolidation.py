"""Consolidation: many requirements, one honest backlog row per item (ADR-004).

Pure Python. The service layer gathers the facts (requirements, on-hand,
on-order, current estimates, the workspace order) and this module turns them
into rows; every rule here is a named test in ``tests/test_consolidation.py``.

For each catalog item, over requirements with intent ``active``:

- consumable: needed = Σ qty - Σ consumed; every requirement adds.
- reusable, shared: needed = max qty across shared requirements (one set moves
  between projects, so the workspace needs enough for the largest demand).
- reusable, exclusive: each adds qty - consumed against it (installing it is
  the consumption).
- gross shortfall = needed - on-hand, floored at zero; rows at zero leave.

On-order is shown beside gross shortfall as its own column. Money figures (line
totals, the "still to buy" total) use the net shortfall, gross minus on-order,
the same quantity the forecast buys; what is already on order is shown
separately as committed money at the purchase's actual prices (ADR-012).
Row priority is the most urgent contributing requirement. Rows follow the
workspace backlog order; items not yet in it append in the default order
(ADR-012): priority, then project, then the contributing requirement's
``sort_order`` (a manifest's file order), then item name.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from projectbudget.domain.catalog import Intent, ItemKind, Sharing
from projectbudget.domain.money import Money, sum_money


@dataclass(frozen=True, slots=True)
class RequirementLine:
    """A requirement as consolidation sees it. ``consumed`` is units used against it."""

    id: str
    project_id: str
    catalog_item_id: str
    qty: int
    priority: int
    sharing: Sharing
    intent: Intent
    consumed: int = 0
    sort_order: int = 0
    """Position within its project: manifest order for synced rows, creation order by hand."""

    @property
    def active(self) -> bool:
        return self.intent is Intent.ACTIVE

    @property
    def remaining(self) -> int:
        return max(self.qty - self.consumed, 0)


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """What is known about one catalog item, independent of any project."""

    id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    on_hand: int = 0
    on_order: int = 0
    unit_price: Money | None = None
    price_as_of: date | None = None
    on_order_cost: Money | None = None
    """What the unreceived ordered quantity cost on its purchase lines (actual prices)."""


@dataclass(frozen=True, slots=True)
class BacklogRow:
    item: ItemFacts
    needed: int
    gross_shortfall: int
    priority: int
    requirement_ids: tuple[str, ...]
    project_ids: tuple[str, ...]
    shared: bool
    """True when a shared reusable requirement contributes; the UI shows the assumptions."""

    @property
    def on_hand(self) -> int:
        return self.item.on_hand

    @property
    def on_order(self) -> int:
        return self.item.on_order

    @property
    def net_shortfall(self) -> int:
        """What the forecast would buy: gross shortfall less what is already ordered."""
        return max(self.gross_shortfall - self.item.on_order, 0)

    @property
    def unit_price(self) -> Money | None:
        return self.item.unit_price

    @property
    def line_total(self) -> Money | None:
        """Net shortfall at the current estimate, or None when unpriced (ADR-012).

        Zero for a priced row that is fully on order: nothing more to buy for it.
        """
        if self.item.unit_price is None:
            return None
        return self.item.unit_price * self.net_shortfall

    @property
    def on_order_cost(self) -> Money | None:
        """Money already committed on open orders for this item, at the purchases' prices."""
        return self.item.on_order_cost

    @property
    def fully_ordered(self) -> bool:
        """Every missing unit is on order: the row stays visible, marked, until received."""
        return self.item.on_order > 0 and self.net_shortfall == 0

    @property
    def priced(self) -> bool:
        return self.item.unit_price is not None


@dataclass(frozen=True, slots=True)
class Backlog:
    currency: str
    rows: tuple[BacklogRow, ...]
    """Priced rows with gross shortfall > 0, in backlog order."""
    unpriced: tuple[BacklogRow, ...]
    """The "needs a price" bucket: counted, never summed, never scheduled."""

    @property
    def total(self) -> Money:
        """Still to buy: priced net line totals. A lower bound whenever ``unpriced`` is non-empty."""
        return sum_money((row.line_total for row in self.rows if row.line_total), self.currency)

    @property
    def committed(self) -> Money:
        """Money already on open orders for the rows shown, at the purchases' actual prices."""
        return sum_money(
            (row.on_order_cost for row in (*self.rows, *self.unpriced) if row.on_order_cost),
            self.currency,
        )

    @property
    def unpriced_count(self) -> int:
        return len(self.unpriced)

    @property
    def is_lower_bound(self) -> bool:
        return self.unpriced_count > 0

    @property
    def order(self) -> tuple[str, ...]:
        """Item ids in display order, priced then unpriced; what the UI reorders."""
        return tuple(row.item.id for row in (*self.rows, *self.unpriced))


def needed_quantity(kind: ItemKind, lines: Sequence[RequirementLine]) -> int:
    """Units the workspace needs for these active requirements, before on-hand."""
    active = [line for line in lines if line.active]
    if kind is ItemKind.CONSUMABLE:
        return sum(line.remaining for line in active)
    shared = max((line.qty for line in active if line.sharing is Sharing.SHARED), default=0)
    exclusive = sum(line.remaining for line in active if line.sharing is Sharing.EXCLUSIVE)
    return shared + exclusive


def gross_shortfall(kind: ItemKind, lines: Sequence[RequirementLine], on_hand: int) -> int:
    return max(needed_quantity(kind, lines) - on_hand, 0)


def row_priority(lines: Sequence[RequirementLine]) -> int:
    """The most urgent (lowest number) priority among active contributing requirements."""
    return min(line.priority for line in lines if line.active)


def default_rank(
    lines: Sequence[RequirementLine], project_position: dict[str, int]
) -> tuple[int, int, int]:
    """Where a row falls by default: its best contributing line's (priority, project, order).

    Projects not in ``project_position`` rank after those that are. The row's priority is
    that of its most urgent line, so the first element equals ``row_priority``.
    """
    unknown = len(project_position)
    return min(
        (line.priority, project_position.get(line.project_id, unknown), line.sort_order)
        for line in lines
        if line.active
    )


def consolidate(
    items: Iterable[ItemFacts],
    lines: Iterable[RequirementLine],
    *,
    order: Sequence[str],
    currency: str,
    project_order: Sequence[str] = (),
) -> Backlog:
    """Build the backlog: one row per item with gross shortfall > 0, in workspace order.

    ``order`` is the owner's explicit backlog order (item ids); rows outside it follow the
    default order, where ``project_order`` (project ids, Projects page order) breaks ties
    between projects.
    """
    project_position = {project_id: index for index, project_id in enumerate(project_order)}
    by_item: dict[str, list[RequirementLine]] = {}
    for line in lines:
        if line.active:
            by_item.setdefault(line.catalog_item_id, []).append(line)

    rows: list[BacklogRow] = []
    rank: dict[str, tuple[int, int, int]] = {}
    for item in items:
        contributing = by_item.get(item.id, [])
        if not contributing:
            continue
        shortfall = gross_shortfall(item.kind, contributing, item.on_hand)
        if shortfall == 0:
            continue
        rank[item.id] = default_rank(contributing, project_position)
        rows.append(
            BacklogRow(
                item=item,
                needed=needed_quantity(item.kind, contributing),
                gross_shortfall=shortfall,
                priority=row_priority(contributing),
                requirement_ids=tuple(sorted(line.id for line in contributing)),
                project_ids=tuple(sorted({line.project_id for line in contributing})),
                shared=item.kind is ItemKind.REUSABLE
                and any(line.sharing is Sharing.SHARED for line in contributing),
            )
        )

    position = {item_id: index for index, item_id in enumerate(order)}
    rows.sort(
        key=lambda row: (
            position.get(row.item.id, len(position)),
            rank[row.item.id],
            row.item.name.lower(),
            row.item.id,
        )
    )
    return Backlog(
        currency=currency,
        rows=tuple(row for row in rows if row.priced),
        unpriced=tuple(row for row in rows if not row.priced),
    )
