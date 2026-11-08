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

On-order is shown beside gross shortfall and netted out only by the forecast.
Row priority is the most urgent contributing requirement. Rows follow the
workspace backlog order; items not yet in it append in priority order.
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
        """Gross shortfall at the current estimate, or None when unpriced (never zero)."""
        if self.item.unit_price is None:
            return None
        return self.item.unit_price * self.gross_shortfall

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
        """Sum of priced line totals. A lower bound whenever ``unpriced`` is non-empty."""
        return sum_money((row.line_total for row in self.rows if row.line_total), self.currency)

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


def consolidate(
    items: Iterable[ItemFacts],
    lines: Iterable[RequirementLine],
    *,
    order: Sequence[str],
    currency: str,
) -> Backlog:
    """Build the backlog: one row per item with gross shortfall > 0, in workspace order."""
    by_item: dict[str, list[RequirementLine]] = {}
    for line in lines:
        if line.active:
            by_item.setdefault(line.catalog_item_id, []).append(line)

    rows: list[BacklogRow] = []
    for item in items:
        contributing = by_item.get(item.id, [])
        if not contributing:
            continue
        shortfall = gross_shortfall(item.kind, contributing, item.on_hand)
        if shortfall == 0:
            continue
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
            row.priority,
            row.item.name.lower(),
            row.item.id,
        )
    )
    return Backlog(
        currency=currency,
        rows=tuple(row for row in rows if row.priced),
        unpriced=tuple(row for row in rows if not row.priced),
    )
