# Changelog

All notable changes to ProjectBudget. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/). A release's section becomes its GitHub Release notes.

## [Unreleased]

### Added

- `PROJECTBUDGET_GITHUB_TOKEN`: one read-only token for every private GitHub sync source, read from the environment or `.env` and never stored; a source's own token variable overrides it. The operations guide has the fine-grained token recipe and where the token goes for `uvx`, Docker and Compose (#30).
- A source's branch, path, token variable and CSV project id can be changed on the source page and with `PATCH /api/v1/sync/sources/{id}`; the source page says which token it will use (#29, #31).
- A sync source can be attached to an existing project (chosen on the Add source form or with `project_id` on the API). On the first apply, hand-entered requirements whose catalog item the manifest also names are adopted, keeping intent, sharing, notes and consumption; the plan lists adoptions as their own action and warns when a source with no project chosen would create a second project with the same name (#26, ADR-012).
- Browser smoke tests (`tests/e2e`, Playwright, Chromium) that sign in, run the core journey, check destructive buttons ask first and fail on console errors, at desktop and phone width; CI runs them as "UI smoke (Playwright)".

### Changed

- The backlog's money figures (line totals and "Still to buy") count only what is not yet on order, the same quantity the timeline buys, so the two pages no longer disagree by the value of open orders. A "Committed on order" tile shows the money on every open order at the purchases' prices, whether or not its item still shows, and a fully ordered row carries an "on order" badge and a 0.00 line total, with or without an estimate, until it is received (#25, ADR-012).
- Default backlog and timeline order within a priority tier is now by project (creation order) and then each project's own requirement order instead of alphabetical; a manifest sets that order to its file order on every apply, so the file's order is the purchase order (#34, ADR-012). The owner's explicit backlog order still wins.

### Fixed

- The timeline no longer shows a red negative "opening balance" or says "blocked" when the only shortfall is the protected reserve and this month's contribution covers it. The first tile is now what is spendable (floored at zero) beside the reserve, the balance basis is one line under the tiles, "blocked" means the first month still cannot buy after its contribution, and the budget page says the contribution is counted in full this month so a balance entered after payday should be the post-payday figure (#21).
- A sync reject for a kind or unit the manifest may not change now names the actual reason (confirmed, merged, created by hand, or used by a requirement, purchase, stock or price), links the catalog item and states the two ways out; when every reject shares one cause the preview says so once above the table (#28).
- A GitHub 404 now says that GitHub reports a private repository as not found without a token and how to set one, instead of a bare status code; the token variable field no longer shows a plausible value as its placeholder (#29, #31).
- The sync sources table and a source's runs table stack at phone width like the other tables, so the source page no longer scrolls sideways.
- Phone width: the navigation wraps, inline form fields fill the line one per row, and the backlog, timeline, requirements, catalog and purchases tables become labelled stacked rows below 640 px, so nothing scrolls sideways and the shortfall and price columns stay on screen (#15).
- Static asset links carry the app version, so a browser never keeps an old stylesheet or script after an upgrade.
- Destructive buttons (merge, cancel purchase, remove line, detach source, remove override, import) ask for confirmation first, and the merge form no longer preselects a target, so one stray click cannot merge two unrelated items (#17).

## [0.1.0] - 2026-11-08

The first release: the complete V1 scope of RFC v3, self-hosted as one process.

### Added

- Single-owner login with Argon2id passwords, database-backed sessions, CSRF protection, a database-backed login rate limit, security headers with a strict Content Security Policy, and hashed, revocable API tokens (read-only or read-write).
- Projects, catalog items (consumable or reusable, with a stable slug and unit), requirements with priority tiers, sharing and intent, and price estimates with as-of date and confidence; money is exact integer minor units in one workspace currency.
- The consolidated backlog: shared tools counted once at the largest demand, parts added up, stock and on-order quantities shown, unpriced items in their own bucket with totals labelled as lower bounds, and a workspace-level order.
- The budget plan (balance as of a date, protected reserve, monthly contribution, horizon, contingency, month overrides) and the explainable timeline: a pure forecast with an optimistic and a conservative schedule, month-by-month opening, contribution, overrides, purchases and closing, and every guard from the RFC (not fundable, beyond horizon, stale estimate, blocked balance, stale balance, unpriced warning).
- Purchases with lines, partial receipt into stock, status lifecycle, one-click "mark purchased" from the timeline; inventory movements with adjustments and consumption against a requirement; catalog merge with redirects.
- Export and import of the whole workspace as one versioned JSON document, id for id, from the Data page, the API and the CLI; `projectbudget backup` for a consistent SQLite copy.
- Sync of `project-budget.yaml` manifests from files under an import directory, fixed-column CSV, or GitHub repositories, with a dry-run plan, one-transaction apply, archive-on-missing and a run history; a JSON Schema for the manifest.
- A JSON API under `/api/v1` mirroring every page action, with the OpenAPI document behind login.
- `projectbudget run --demo` to try the app on fictional data; a Docker image with SQLite on a volume and an optional Compose file with PostgreSQL.

[Unreleased]: https://github.com/sdeery14/project-budget/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sdeery14/project-budget/releases/tag/v0.1.0
