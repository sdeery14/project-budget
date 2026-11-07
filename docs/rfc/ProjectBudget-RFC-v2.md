# ProjectBudget — Revised RFC & ADRs (v2)

Nov 7, 2026 · @Sean

## 1. Status and what changed from v1

**Status:** Revised proposal, ready for ADR sign-off. No repository exists; nothing here is implemented. Supersedes the 2026-11-07 v1 RFC and the independent review.

| v1 position | v2 decision | Why |
| --- | --- | --- |
| PostgreSQL only, Docker Compose required | SQLite default, PostgreSQL supported (ADR-001) | One-file self-host; backups are a file copy; money is integer minor units so DECIMAL is not needed |
| NiceGUI | Jinja2 + HTMX, JSON API parity, SPA exit path (ADR-002) | Stateless, testable with an HTTP client, runs on any host, no WebSockets |
| Forecast in Milestone 3 | Forecast in Milestone 2, immediately after the backlog (ADR-006) | The forecast is the product thesis; validate it early |
| GitHub manifest sync in Milestone 2 | Basic GitHub + local-file sync stays in V1 (Milestone 4); OAuth, webhooks, private-repo apps deferred (ADR-007) | Keeps the differentiating workflow without an auth integration |
| Reusable items implied qty = 1 | Reusable items support quantities; sharing assumptions explicit (ADR-004) | "3 clamps for A, 2 for B" must consolidate correctly |
| Provisional item or CatalogItem | Every requirement references a CatalogItem; free text creates an unreviewed item | Removes a nullable-FK branch from every query |
| Neon + Render maintainer instance | Deferred; seeded demo mode instead | Hosting is not the product |
| AuditEvent, FundingBucket, ForecastScenario, Allocation | Deferred or folded into simpler entities | Avoidable complexity for V1 |

## 2. Product thesis and V1 scope

ProjectBudget answers one question: **given everything my projects still need, what can I buy next, and when?** It consolidates requirements across projects into one honest shopping backlog and schedules purchases against a manually entered discretionary budget.

**Target user:** a maker, cyclist, homelabber or DIYer running two or more projects with overlapping parts, who will run one process on a laptop, NAS or small VPS. No GitHub, bank or programming knowledge required; GitHub sync is optional.

**Core journey (must work end to end in V1):** create projects → add requirements (free text or picked from the catalog) → see the consolidated backlog with priced subtotal and unpriced count → enter budget inputs → read an explained month-by-month purchase timeline → mark an item purchased → watch it leave the backlog and timeline.

### In V1

- Single-owner login; one workspace; one currency
- Projects, catalog items (consumable or reusable, with unit), requirements with priority tiers
- Price estimates with as-of date and confidence; missing price is missing, never zero
- Consolidated backlog (rules in §4)
- Budget inputs and explainable forecast (rules in §5)
- Purchases with lines, partial receipt, inventory movements, consumption by project
- Sync from a local manifest file, a CSV, or a `project-budget.yaml` in a public GitHub repo (optional read-only token for private repos), with dry-run diff and apply
- Versioned JSON export and import of the whole workspace
- SQLite default; PostgreSQL supported; `uvx`/`pipx` install, single Docker image, optional Compose
- Responsive HTML pages usable on a phone in a store

### Not in V1

GitHub OAuth or GitHub App, webhooks, multi-user or multi-workspace, API token scopes beyond read/write, refunds, requirement dependency graph, unit conversion, multi-currency, scenario comparison, background job queue, price scraping, bank sync, notifications, hosted maintainer instance, mobile app.

### V1 acceptance (release gate)

1. Two seeded projects share one reusable item (A needs 3, B needs 2) and one consumable (A needs 10, B needs 5); with 1 reusable and 4 consumable owned, the backlog shows shortfall 2 reusable and 11 consumable.
2. One unpriced item appears in the "needs a price" bucket; the backlog total is labeled a lower bound and excludes it.
3. The timeline explains each month: opening balance, contribution, purchases, closing balance.
4. Marking an item purchased removes it from backlog and timeline; partial receipt leaves the remainder.
5. Export → wipe → import reproduces an identical backlog and forecast (byte-equal JSON of both views).
6. Re-running the same manifest sync changes nothing; a requirement missing from the manifest is archived, and its purchase history survives.
7. The full test suite passes on SQLite and PostgreSQL in CI.
8. A fresh clone runs via `uvx projectbudget` and via `docker run` with no secrets, and a second person follows the README to a working install.

## 3. Architecture

One Python process, one database file or connection, no background workers. Every read and write goes through the service layer, which is also where authorization will be enforced when multi-user arrives.

&#91;embedded content: runtime architecture · 5 layers, 2 databases\]

Browser and API clients enter through routers; the CLI and sync adapters call services directly. Nothing below the service layer is reachable any other way.

**Layers and rules**

- **Routers (FastAPI):** two thin families, `/` HTML pages and fragments rendered with Jinja2 + HTMX, and `/api/v1` JSON. Both call the same service functions with the same request models; neither contains business logic. Rule: every state-changing UI action has a JSON twin.
- **Services:** the only module allowed to open a session or commit. Functions take a `Principal` (the owner in V1) and typed commands; they return typed results. Transactions begin and end here.
- **Domain:** pure Python. `Money`, consolidation rules, the forecast engine, manifest diffing. No SQLAlchemy imports, no I/O. This is where most unit tests live.
- **Persistence:** SQLAlchemy 2.0 models and Alembic migrations, restricted to the dialect-neutral subset (ADR-001). Integer minor units for money, ISO strings for currency, UUIDs stored as text.
- **Adapters:** manifest sources (local file, CSV, GitHub raw URL) that produce a `SyncPlan`; export/import; nothing else in V1.
- **CLI (`projectbudget`):** `init` (create owner), `db upgrade`, `run`, `export`, `import`, `sync`. The container entrypoint runs `db upgrade` then `run`, so migrations never race between workers.

**Distribution**

| Path | Database | Who it is for |
| --- | --- | --- |
| `uvx projectbudget` / `pipx install` | SQLite in `~/.projectbudget/` (overridable) | Most users; zero infrastructure |
| Single Docker image (GHCR) | SQLite on a mounted volume | NAS and VPS users |
| `docker compose up` | PostgreSQL container | Users who already run Postgres, and the future hosted instance |

**Hosting notes (for whoever runs it on the internet):** put it behind a TLS reverse proxy (Caddy example in docs). If a managed Postgres with a transaction-mode pooler is used, run migrations, `pg_dump` and restore against the direct connection string. A maintainer-run instance is deferred; the demo is `projectbudget run --demo`, which seeds fictional data into a throwaway SQLite file.

## 4. Domain model

A requirement is a want, a purchase is a fact, an inventory movement is a fact, and fulfillment is derived, never stored. Nine entities cover V1.

| Entity | Key fields | Owner | Notes |
| --- | --- | --- | --- |
| Project | name, archived, notes, source\_ref? | App (name/description from source if synced) | source\_ref = (source id, external project id) |
| CatalogItem | name, kind (consumable, reusable), unit, review\_state (unreviewed, confirmed), merged\_into? | App | Created automatically from free text; merge is an explicit action that re-points every reference |
| Requirement | project, catalog\_item (not null), qty, priority (1–3), sort\_order, intent (active, paused, cancelled), sharing (shared, exclusive), source\_ref?, notes | Split: source owns qty, priority, name, external id; app owns catalog mapping, intent, sharing, notes | Unit must equal the catalog item's unit |
| PriceEstimate | catalog\_item, amount (minor units), currency, as\_of, confidence (low, medium, high), origin (manual, manifest, purchase) | App | Latest `as_of` wins; none means unpriced |
| Purchase | vendor, ordered\_on, status (planned, ordered, received, cancelled), shipping, tax, total | App, never touched by sync | Shipping and tax stay on the header; never allocated to lines |
| PurchaseLine | purchase, catalog\_item, qty, unit\_price, received\_qty, received\_on? | App | Receiving a line creates an inventory movement |
| InventoryMovement | catalog\_item, delta\_qty, reason (received, consumed, adjustment), purchase\_line?, requirement?, occurred\_on | App | On-hand = sum of deltas; consumption against a requirement is the allocation |
| BudgetPlan | currency, earmarked\_funds, protected\_reserve, monthly\_contribution, horizon\_months, contingency\_pct, MonthOverride\[\] (month, delta, label) | App, private | Exactly one per workspace in V1 |
| SyncSource / SyncRun | kind (file, csv, github), locator, token\_ref?, last\_revision, content\_hash, summary | App | Revision = git commit SHA or file hash |

### Consolidation rules (ADR-004)

For each catalog item, over requirements with intent = active:

- **Consumable:** shortfall = Σ qty − on-hand − on-order − already consumed against those requirements. Consumables are never shared; every requirement adds.
- **Reusable, sharing = shared:** needed = **max** qty across shared requirements. Assumption: shared reusable items are used by one project at a time and move between projects, so the workspace needs enough for the largest single demand, not the sum.
- **Reusable, sharing = exclusive:** each exclusive requirement adds its full qty (the item stays with that project, e.g. permanently installed).
- **Reusable total:** needed = max(shared) + Σ exclusive; shortfall = needed − on-hand − on-order.
- Shortfall floors at zero. Items with shortfall 0 leave the backlog.

Worked example: clamps are reusable. A needs 3 (shared), B needs 2 (shared), C needs 1 (exclusive). Needed = max(3, 2) + 1 = 4. With 1 owned, shortfall = 3.

Documented assumptions shown in the UI beside any shared reusable item: time overlap between projects is not modeled; "shared" is a workspace-wide default that a requirement can override to exclusive; on-hand counts every unit regardless of which project last used it.

### Invariants (each is a named test)

- Money is an integer in minor units with a currency code; mixed currencies are rejected at write.
- A requirement always references a catalog item; units match.
- A purchase never implies project membership; consumption does.
- Cancelled requirements leave the shortfall but keep their history and consumption records.
- Merging catalog items re-points requirements, lines, movements and estimates in one transaction and leaves a redirect.
- Unpriced items never contribute zero; totals carry an unpriced count.
- On-hand never goes negative through consumption; adjustments may correct it with a reason.
- Same content hash on sync is a no-op; a missing manifest entry archives, never deletes.

## 5. Forecast semantics (ADR-006)

The forecast is a pure function: `(BudgetPlan, backlog, today) → Schedule`. It touches no database and is deterministic, so every rule below is a golden test.

**Inputs:** backlog items with shortfall > 0 and a current price estimate; the budget plan; today's date.

**Purchase unit:** one backlog row = one indivisible purchase: shortfall qty × latest unit estimate × (1 + contingency %). No partial buys in V1.

**Queue order:** priority tier ascending, then the user's sort order. Strict order; an item is never skipped because a cheaper one behind it would fit. (A "fill gaps" toggle is deferred; strict order is the rule a user can verify by eye.)

**Monthly loop, starting this month:**

1. Opening balance = previous closing (month 1: earmarked funds − protected reserve).
2. Add monthly contribution, then apply this month's overrides (signed, labeled).
3. While the head of the queue costs ≤ balance: buy it, subtract, record.
4. Closing balance carries forward. Record opening, contribution, overrides, purchases and closing for the month; that record is the explanation shown in the UI.

**Exclusions and buckets**

- Unpriced items go to a separate "needs a price" bucket, counted but never scheduled and never summed. If an unpriced item sits ahead of scheduled items in priority, the timeline shows a warning that it is optimistic.
- Items with on-order quantity covering the shortfall are excluded (money already committed).
- Shared reusable items are scheduled once; per-project views show "exclusive" and "including shared" costs separately and never sum projects to a grand total.

**Guards against false confidence**

| Condition | Shown as |
| --- | --- |
| Monthly contribution ≤ 0 and balance < next item | "Not fundable at current inputs", no date |
| Item not reached within horizon (default 36 months) | "Beyond horizon" |
| Estimate older than 90 days | Stale flag on the item and its month |
| Opening balance < 0 after reserve | Timeline blocked until balance ≥ 0, with the month it recovers |
| Any unpriced item | Banner with count; totals labeled "lower bound" |

**Uncertainty in V1:** contingency % produces two schedules, optimistic (0 %) and conservative (contingency applied); both dates are shown per item. The page carries a fixed line: "Planning projection from your inputs; not financial advice."

## 6. Sync contract (ADR-007)

Sync is a user-triggered, read-only pull of a `project-budget.yaml` from one of three sources, previewed as a diff and applied in one transaction. Purchases, inventory and budget data are never touched by sync.

**Sources in V1**

| Source | Locator | Auth | Revision |
| --- | --- | --- | --- |
| Local file | path under a configured import directory, or an upload | none | SHA-256 of content |
| CSV | same as local file; fixed columns id, name, qty, unit, kind, priority, estimate | none | SHA-256 of content |
| GitHub | `owner/repo`, branch, path (default `project-budget.yaml`) via the Contents API | none for public repos; optional fine-grained read-only token from an environment variable for private repos | commit SHA of the file |

Only `api.github.com` and `raw.githubusercontent.com` are reachable from the sync adapter; arbitrary URLs are not accepted in V1, which closes the SSRF question without an allowlist engine.

**Manifest v1 (fields; a JSON Schema ships in the repo)**

```yaml
version: 1
project:
  id: hcalory-heater        # stable slug, required
  name: Diesel heater install
items:
  - id: fuel-line-6mm        # stable within the project, required
    name: 6 mm fuel line
    qty: 3
    unit: m
    kind: consumable          # consumable | reusable, default consumable
    priority: 2               # 1..3, default 2
    sharing: shared           # reusable only: shared | exclusive
    estimate: { amount: 12.50, currency: USD }   # optional
    catalog: fuel-line-6mm    # optional workspace catalog slug
    notes: ""
```

**Identity:** (source, external project id, external item id). Names never identify anything. Duplicate ids in one manifest reject the whole file.

**Field ownership on a synced requirement**

- Source owns: name, qty, unit, kind, priority, estimate (recorded as a PriceEstimate with origin = manifest), sharing default.
- App owns: catalog mapping, intent (active/paused/cancelled), sharing override, notes, sort order.
- Source wins on source fields, app wins on app fields; there is no three-way merge. "Detach from source" converts a project to manual and keeps everything.

**Reconciliation rules**

| Situation | Result |
| --- | --- |
| Same content hash or commit SHA as last run | No-op, run recorded |
| New item id | Create requirement; create unreviewed catalog item unless `catalog` matches an existing slug |
| Existing id, changed fields | Update source-owned fields only |
| Id missing from manifest | Requirement intent → archived (`removed_from_source`); consumption and purchases untouched |
| Id reappears | Un-archive the same requirement |
| Renamed item, same id | Update name |
| Same name, new id | New requirement plus archive of the old one; the diff flags the likely rename |
| Invalid record | Whole file rejected with line-level errors; nothing applied |
| Kind or unit changed on an item with history | Rejected for that item; user must resolve in the UI |

**Dry-run:** every sync produces a `SyncPlan` (creates, updates, archives, re-activations, rejects) that the UI shows before apply. Apply is one transaction; partial failure rolls back and the run is recorded as failed.

## 7. Security requirements (ADR-005)

V1 protects one owner's private budget data on an instance that may be reachable from the internet. Everything in the first list blocks release; the second list is V1.x.

**Release blockers**

- Owner created by `projectbudget init`; no default credentials; the app refuses to serve until an owner exists.
- Passwords hashed with Argon2id (`argon2-cffi` or `pwdlib`; not passlib).
- Session cookie: random id stored server-side, HttpOnly, Secure (configurable off for localhost), SameSite=Lax, idle timeout, rotated on login.
- CSRF: Origin/Referer check on every state-changing request plus a per-session token in HTMX forms.
- Login rate limit (in-process; no Redis) and constant-time comparison.
- `require_owner` dependency on every router except `/health`, `/login` and static assets; a test asserts this by walking the route table.
- API tokens: random, shown once, stored as SHA-256 hash, revocable, flag read-only or read-write. No finer scopes in V1.
- Security headers (CSP without inline scripts, X-Content-Type-Options, frame-ancestors none, Referrer-Policy).
- Imports: `yaml.safe_load`, 1 MB size limit, 10 s fetch timeout, GitHub hosts only, CSV cells starting with `= + - @` prefixed on export.
- Secrets only from environment or an env file outside the repo; `.env.example` with placeholders; repo-wide secret scanning in CI.
- Database user limited to its own database (Postgres path); SQLite file mode 0600.
- Backups: documented `projectbudget export` (JSON) plus file copy or `pg_dump`; a restore drill is part of the release checklist.
- No telemetry, no external calls except GitHub sync when the user triggers it.

**V1.x and later**

- Multi-user and workspace isolation, enforced in the service layer by `Principal` (the chokepoint already exists in V1).
- GitHub App or OAuth for private repos; webhook HMAC verification, delivery-id dedupe, replay handling.
- Scoped API tokens per project; audit log; encrypted at-rest token storage in the database.
- Threat model document covering credential leakage, cross-user exposure, malicious manifest, webhook forgery, compromised dependency, backup loss — a two-page draft is written in Milestone 5, expanded before any hosted offering.

## 8. Testing, CI and repository conventions

The domain layer is tested without a database, the service layer against both databases, and the UI through an HTTP client; a browser is not required for CI.

| Layer | Tool | What is covered |
| --- | --- | --- |
| Domain (pure) | pytest, hypothesis for money and consolidation | Money arithmetic, consolidation rules, forecast golden cases, manifest diffing |
| Services + persistence | pytest against SQLite and a PostgreSQL service container, both in the CI matrix | Transactions, invariants, merge, sync apply, export/import round-trip |
| Migrations | Alembic upgrade from empty on both backends; autogenerate must produce no diff; downgrade one step | Schema drift |
| HTTP | httpx TestClient | Every route requires the owner (route-table walk), CSRF, JSON/HTML parity on state changes |
| Browser smoke | Playwright, one flow, runs nightly not per PR | Core journey end to end |
| Security | pip-audit, Dependabot, gitleaks, bandit | Dependencies and secrets |

**Fixtures:** one canonical fictional workspace (`tests/fixtures/workspace.json`) used by unit tests, the demo mode and the README screenshots. No real data anywhere in the repo, issues or CI logs.

**Repository conventions**

- `uv` lockfile; Python 3.12+; `ruff` (lint + format), `mypy --strict` on domain and services.
- `src/projectbudget/{domain,services,persistence,adapters,web,api,cli}`; `docs/adr/NNN-title.md`; `docs/manifest-v1.schema.json`.
- `main` protected; squash merges; each PR names the ADR it implements; CI must pass on both databases.
- Conventional commits; version from git tag; GitHub Release and GHCR image on tag.
- License Apache-2.0 (ADR-009); no CLA; `SECURITY.md` with a private disclosure address; `CONTRIBUTING.md` with the one-command dev setup.
- Codex reviews PRs against the ADR named in the PR, not against general preference.

## 9. Architecture decision records

All eleven are proposed as **Accepted** on sign-off of this document; each becomes `docs/adr/NNN-*.md` in PR1 with the text below.

**ADR-001 Database: SQLite default, PostgreSQL supported.** Context: self-hosters are hobbyists; a hosted instance may come later. Decision: SQLAlchemy 2.0 against the dialect-neutral subset (no JSONB, arrays, partial indexes, Postgres enums; UUIDs as text; money as integers; Alembic batch mode for SQLite). CI runs both. Consequences: trivially simple self-host and backup; a small feature tax on Postgres; no SQLite-only mode and no Postgres-only features until a hosted offering justifies them.

**ADR-002 UI: Jinja2 + HTMX with JSON API parity.** Context: forms, tables, one timeline chart; must run stateless on cheap hosts; a solo maintainer. Decision: server-rendered pages and HTMX fragments; a vendored, no-build JS chart library for the timeline; every state change also exposed under `/api/v1`. Exit path: a SPA can replace the templates without touching services. Consequences: no WebSockets, testable with an HTTP client, slightly less fluid grid editing.

**ADR-003 Money: integer minor units, one workspace currency.** Decision: `Money(amount: int, currency: str)`; the workspace currency is set at `init`; writes in another currency are rejected. Consequences: no FX, no rounding drift, works on SQLite.

**ADR-004 Item kind and consolidation.** Decision: `CatalogItem.kind` is consumable or reusable; reusable requirements carry `sharing` = shared (default) or exclusive; needed = Σ consumables, max(shared reusable) + Σ exclusive reusable; shortfall subtracts on-hand and on-order. Sharing assumptions are displayed in the UI (§4). Consequences: correct backlog for tools and parts alike; time-overlap between projects is explicitly not modeled.

**ADR-005 Auth: single owner now, principal chokepoint for later.** Decision: one owner, session cookies, hashed API tokens with read-only/read-write flag; every service function receives a `Principal`; no Workspace entity in V1. Migration cost to multi-user: add `workspace_id` to root tables with a single backfilled value and enforce filtering in the service layer. Consequences: no premature tenancy; authorization has one place to live.

**ADR-006 Forecast V1 semantics.** Decision: as specified in §5; implemented as a pure, deterministic function with golden tests; contingency % yields optimistic and conservative dates. Consequences: explainable by construction; no optimization or reordering.

**ADR-007 Sync contract.** Decision: manifest v1 as in §6; sources are local file, CSV and GitHub (public, or private via an environment token); identity by external ids; field-level ownership; archive on missing; dry-run then transactional apply; GitHub hosts only. Deferred: OAuth, GitHub App, webhooks, arbitrary URLs.

**ADR-008 Canonical export format.** Decision: a versioned JSON document of the whole workspace, written by `projectbudget export` and read by `import`, used for backup, SQLite↔Postgres migration, demo seeding and test fixtures. Consequences: one serializer to maintain; every entity must round-trip.

**ADR-009 License: Apache-2.0, no CLA.** Reason: explicit patent grant and NOTICE handling; MIT remains acceptable if simplicity is preferred, but decide in PR1.

**ADR-010 Distribution.** Decision: PyPI package runnable with `uvx`/`pipx`, a single GHCR image with SQLite on a volume, and an optional Compose file with Postgres; the container entrypoint runs migrations then starts; no in-app concurrent migrations.

**ADR-011 Deferred list is binding.** Decision: items in §2 "Not in V1" require a new ADR to move earlier. Consequences: scope creep has a cost everyone can see.

## 10. Phased roadmap

Six milestones, each with a gate that must pass before the next starts. Forecasting lands in Milestone 2 so the product thesis is tested before procurement and sync plumbing.

&#91;embedded content: roadmap · 6 milestones, 5 gates\]

| Milestone | Delivers | Gate |
| --- | --- | --- |
| M0 Skeleton | Repo, ADRs, CI on both databases, app factory, migrations, CLI, Docker image, owner auth | Clean clone runs, logs in, passes an empty test suite on SQLite and Postgres |
| M1 Backlog | Projects, catalog items, requirements, price estimates, consolidation, backlog page and JSON | Acceptance tests 1 and 2 pass |
| M2 Forecast | Budget plan, pure forecast engine, timeline page, minimal "mark purchased" | Acceptance tests 3 and 4 pass; golden tests for every guard in §5 |
| M3 Purchases and inventory | Purchases, lines, partial receipt, movements, consumption, catalog merge, JSON export/import | Acceptance test 5 passes; merge invariant test passes |
| M4 Sync | Manifest schema, file/CSV/GitHub adapters, dry-run diff, apply, archive-on-missing | Acceptance test 6 passes; idempotence test passes |
| M5 Release | Threat-model draft, backup and restore drill, docs, demo mode, accessibility pass, PyPI and GHCR release | Acceptance tests 7 and 8 pass; an unaffiliated person self-hosts from the README |

**Later (V1.x, each by new ADR):** GitHub App or OAuth for private repos, webhooks, fill-gaps forecast toggle, scenario comparison, requirement dependencies, refunds, unit conversion, multi-user, scoped tokens, hosted instance.

## 11. First five pull requests

Each PR is scoped to land in one review session, names its ADRs, and leaves `main` deployable.

| PR | Title | Contents | ADRs | Done when |
| --- | --- | --- | --- | --- |
| 1 | Repository foundation | LICENSE, README (thesis, quickstart placeholders), `pyproject` + `uv.lock`, ruff, mypy, pytest, pre-commit, CI workflow with SQLite and Postgres jobs, Dependabot, gitleaks, SECURITY.md, CONTRIBUTING.md, `docs/adr/001–011` | 009, 011 | CI green on an empty package; ADRs readable on GitHub |
| 2 | App skeleton, database, CLI, container | App factory, pydantic-settings, SQLAlchemy 2.0 engine for both dialects, Alembic with batch mode and an empty first migration, `/health`, `projectbudget db upgrade` and `run`, Dockerfile with migrate-then-run entrypoint, optional Compose, migration round-trip test | 001, 010 | `uvx` and `docker run` both answer `/health`; migration tests pass on both backends |
| 3 | Owner auth and base UI | `projectbudget init`, Argon2id hashing, server-side sessions, login/logout pages on the Jinja2 + HTMX base layout, CSRF, security headers, login rate limit, `require_owner`, route-table test, API token issue/revoke | 002, 005 | Every route except health, login and static returns 401/403 unauthenticated |
| 4 | Domain core and backlog | `Money`, Project, CatalogItem (kind, unit), Requirement (priority, sharing), PriceEstimate; service layer with `Principal`; consolidation rules; backlog page and `/api/v1` CRUD; canonical fixture; invariant tests on both backends | 003, 004 | Acceptance tests 1 and 2 pass |
| 5 | Forecast engine and timeline | Pure forecast module with golden tests for each guard in §5; BudgetPlan and MonthOverride; timeline page with per-month explanation; vendored chart; minimal "mark purchased" that records a Purchase, a line and a received movement | 006 | Acceptance tests 3 and 4 pass |

PR6 onward: purchases and inventory in full, catalog merge, export/import (ADR-008), then the sync adapters (ADR-007).

## 12. Open questions before PR1

Four choices are yours; everything else in this document is settled unless you object.

- [ ] Workspace currency for the demo fixture and docs (assumed USD).
- [ ] License: Apache-2.0 as proposed, or MIT.
- [ ] Private GitHub repos in V1 via an environment token, or public repos only until OAuth.
- [ ] Default `sharing` for reusable items: shared (proposed) or exclusive.

**Explicitly deferred, by ADR-011:** everything under "Not in V1" in §2, the hosted maintainer instance, and the full threat model. Moving any of them earlier requires a new ADR that names what it displaces.
