# ADR-004: Item kind and consolidation

**Status:** Accepted, amended by ADR-012 (default order within a priority tier; money figures net of on-order)
**Date:** 2026-11-07
**Implements:** RFC v3 §4 (Domain model, Consolidation rules, Invariants)

## Context

The product thesis is one honest backlog across projects. Parts are consumed; tools are reused. "3 clamps for project A and 2 for project B" must not become 5 clamps if one set moves between projects, and must become 5 if each project keeps its own. A consolidated row merges several requirements, so it needs a single priority and a single place in the queue.

## Decision

- `CatalogItem.kind` is `consumable` or `reusable`. Reusable requirements carry `sharing` = `shared` (default) or `exclusive`.
- For each catalog item, over requirements with intent `active`:
  - Consumable: gross shortfall = Σ qty − Σ consumed against those requirements − on-hand.
  - Reusable: needed = max(shared qty) + Σ (exclusive qty − consumed against that requirement); gross shortfall = needed − on-hand.
  - Gross shortfall floors at zero.
- **Shared reusable requirements are never consumed against.** The service layer rejects such a movement.
- **On-order** (unreceived quantity on purchases in `ordered` status) is a separate backlog column. The backlog shows gross shortfall; only the forecast (ADR-006) nets on-order out. An ordered item stays visible until received.
- **Row priority** is the most urgent priority among the active contributing requirements. **Backlog order** is a workspace-level list on the budget plan; per-requirement `sort_order` orders only that project's view.
- The UI shows the sharing assumptions beside any shared reusable item: time overlap between projects is not modeled; shared is the default and a requirement can override to exclusive; permanently installed parts are better modeled as consumable; on-hand counts every unit regardless of which project last used it.

## Consequences

- Correct backlog for tools and parts alike. Installing an exclusive item (consuming it) does not raise the shortfall.
- Time overlap between projects is explicitly not modeled.
- Each rule above is a named test; the worked example in the RFC (A needs 3 shared, B needs 2 shared, C needs 1 exclusive, 1 owned, shortfall 3) is a golden case.
