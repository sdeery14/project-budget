# ADR-012: Per-project lists against one budget

**Status:** Proposed
**Date:** 2026-11-11
**Implements:** Owner Acceptance Testing findings #32, #34, #25, #26, #27 and #36 (milestone "Owner Acceptance Testing"); amends RFC v3 §2 and §4, ADR-004 and ADR-007

## Context

Owner Acceptance Testing (issue #11) put two real projects through every page: a bicycle garage and a home-automation build, each with its own `project-budget.yaml` in a private GitHub repository. The owner's verdict on the product thesis was clear and differs from RFC v3 §2:

- The pain is keeping several per-repository shopping lists honest against one discretionary budget. The two-project backlog "showing me all the highest priority items" and the month-by-month table were the pages that earned their keep.
- Item-level consolidation across projects, the headline of RFC v3 §2 and ADR-004, was not the value. The owner's real projects share almost no parts.
- For synced projects the manifest in the repository is the source of truth. The app is the source of truth only for projects entered by hand.

Testing also exposed four rules that follow from the old framing and produce wrong or contradictory output under the new one:

1. The default queue order within a priority tier is alphabetical by item name (`domain/consolidation.py`), so the schedule bought a Raspberry Pi's cooler and power supply a month before the Pi (#34). The manifest's item order, which is the author's purchase order, is discarded because ADR-007 makes sort order app-owned.
2. The backlog's "Still to buy" total counts quantities already on order while the Timeline has already treated that money as spent (#25). ADR-004 chose gross shortfall for the backlog deliberately; one click apart, the two numbers read as a bug.
3. A sync source always creates its own project, so adopting a manifest for a project that was first built by hand duplicates it (#26). Under "manifest first" this is the normal path, not an edge case.
4. Dates the app derives (file mtime, commit date) are UTC dates, which ran a day ahead of the owner (#27). Review state exists to protect kind and unit from manifests but nothing explains it, and confirming nineteen items one page at a time will not happen (#36).

## Decision

**Thesis.** ProjectBudget schedules several projects' shopping lists against one budget and shows what to buy next, and when. The backlog is one priority queue across projects. Consolidation per catalog item (ADR-004) remains the mechanism that keeps the queue honest when projects do share an item; it is no longer the product's headline and the backlog copy stops presenting it as such. RFC v3 §2's first paragraph is read as amended by this ADR.

**Queue order.** The default backlog order is: row priority ascending, then project (by the project's position on the Projects page, creation order until a project order exists), then the contributing requirement's `sort_order`, then item name. The owner's explicit moves (the workspace backlog order on the budget plan) still override the default, as today. On every apply, a manifest sets `sort_order` for the requirements it owns to the item's position in the file; the manifest guide states that the file's order is the purchase order. This amends ADR-007: sort order within a synced project is source-owned; the workspace backlog order stays app-owned.

**One meaning for "still to buy".** The backlog's money figures (tile and line totals) use net shortfall, gross shortfall minus on-order, the same quantity the forecast buys. The row keeps showing gross shortfall and on-order as separate columns, and a row whose net shortfall is zero but on-order is positive stays visible, marked "on order", until received. A second tile shows the money already committed on order. This supersedes the ADR-004 sentence "The backlog shows gross shortfall; only the forecast nets on-order out" for money figures only; quantities are unchanged.

**Attaching a source to an existing project.** A sync source may be created for an existing project, chosen on the Add source form. On the first apply against an existing project, each manual requirement whose catalog item matches a manifest item is adopted: it gains the source's external id, and the source takes ownership of qty, priority and name per ADR-007; intent, sharing override, notes and consumption history are kept. Manual requirements that match nothing stay manual. The preview lists adoptions as their own action. A source with no project chosen creates one, as today, and the preview warns when a project with the manifest's name already exists.

**Dates.** Dates the owner types are stored as written. Dates the app derives from an instant (today, file modification time, commit time) are converted to the workspace timezone before becoming a date. The timezone is a setting (`PROJECTBUDGET_TIMEZONE`, IANA name, default: the server's local zone). Timestamps shown to the owner are converted to the same zone and labelled with it; "UTC" column headings go away.

**Review state.** `confirmed` means "the owner has looked at this item; manifests may no longer change its kind or unit". The catalog page says so where the state is shown, offers a confirm action on each unreviewed row and a filter for unreviewed items, and the app sets `confirmed` automatically when the owner edits the item, records stock for it or buys it. The protection in ADR-007 is unchanged; only how the state gets set changes.

## Consequences

- The backlog and timeline become correct for the owner's real projects without reordering by hand; a manifest author controls purchase order from the repository.
- Three ADRs are amended, not replaced: ADR-004 (money figures net of on-order), ADR-007 (sort order and adoption), and the thesis sentence of RFC v3 §2. Their tests change accordingly; the RFC's worked consolidation example (acceptance test 1) still holds.
- Migration: one revision adds the timezone setting's consumer (none in the schema), `projects.position`, and nothing else; existing `sort_order` values are kept and re-set by the next apply.
- Out of scope, still deferred under ADR-011: per-project budgets, time overlap between projects, a "fill gaps" scheduling toggle, scenario comparison.
- Work that depends on this ADR: #34, #25, #26, #27, #36, and the backlog copy change in #32. None starts until this ADR is accepted.
