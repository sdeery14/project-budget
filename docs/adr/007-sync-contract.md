# ADR-007: Sync contract

**Status:** Accepted, amended by ADR-012 (the source owns sort order within its project; a source may attach to an existing project)
**Date:** 2026-11-07
**Implements:** RFC v3 §6 (Sync contract), Milestone 4

## Context

Keeping a `project-budget.yaml` next to a project is the differentiating workflow, but an OAuth or GitHub App integration is a large surface for a V1. Sync must never corrupt facts (purchases, inventory) or fight the user over fields they edited.

## Decision

- Sync is a user-triggered, read-only pull of manifest v1 from one of three sources: a local file, a CSV (with the external project id configured on the source), or a file in a GitHub repository via the Contents API. Public repos need no auth; private repos use a read-only fine-grained token from an environment variable. Only `api.github.com` and `raw.githubusercontent.com` are reachable.
- Identity is (source, external project id, external item id). Names never identify anything.
- Field ownership: the source owns name, qty, priority, sharing default and the estimate. The source owns kind and unit **only** for a catalog item it created that is still unreviewed; once confirmed, merged or referenced elsewhere, a differing manifest value is a per-item reject. The app owns catalog mapping, intent, sharing override, notes and sort order. No three-way merge. *(ADR-012: sort order within a synced project is now source-owned, set to the item's position in the file on every apply; the workspace backlog order stays app-owned.)*
- Manifest estimates are recorded with origin `manifest` and `as_of` = the file revision date (commit date or file mtime), never the run date, so a newer manual price keeps winning.
- Two error classes: a schema error rejects the whole file with line-level errors; a semantic conflict rejects only that item and the rest of the plan stays applicable.
- A missing item id archives the requirement with reason `removed_from_source`; purchases and consumption are untouched. A reappearing id un-archives the same requirement.
- Every sync produces a `SyncPlan` shown as a dry-run diff. Apply is one transaction. Same content hash or commit SHA is a no-op.
- Purchases, inventory and budget data are never touched by sync.

## Consequences

- The differentiating workflow ships without an auth integration or SSRF surface.
- Deferred by ADR-011: OAuth, GitHub App, webhooks, arbitrary URLs.
