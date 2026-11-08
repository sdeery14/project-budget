# Changelog

All notable changes to ProjectBudget. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/). A release's section becomes its GitHub Release notes.

## [Unreleased]

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
