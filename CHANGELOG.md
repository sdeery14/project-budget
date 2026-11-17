# Changelog

All notable changes to ProjectBudget. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/). A release's section becomes its GitHub Release notes.

## [Unreleased]

### Added

- The README has a "How it works" section that walks the loop once, from adding projects and setting the budget to reading the backlog and timeline and recording a purchase, with a diagram, so a new owner knows the order to use the pages in (#67).
- An agent kit under `docs/agents/`: an `AGENTS.md` section and a Claude Code `/shopping-list` skill to copy into a project repository, so a coding agent builds the shopping list as the manifest, checks it with `manifest validate`, and knows to ask for the export when it cannot reach the server; a test keeps every rule the kit states in step with the parser (#79, ADR-013).
- A project's name and notes can be edited on its page, and a requirement's quantity, priority, sharing and notes on its row, instead of through the API or by cancelling and re-adding (#13, #14). A quantity is refused below what the project has already consumed (`409` on `PATCH /api/v1/requirements/{id}` too). On a synced project the forms offer only what the manifest does not own: the notes, and a row's sharing and notes.
- A favicon: the tab has an icon and the browser console no longer logs a 404 for `/favicon.ico` on every page (#16).
- `projectbudget reset [--yes]`: empty the workspace in one transaction, keeping the owner, sessions, API tokens and currency, instead of deleting the database file and running `init` again (#33).
- An Account page (`/settings/account`) to change the owner's password with the current one, signing every other session out, and a `projectbudget passwd` command on the host for a forgotten password (#12). Neither is reachable with an API token.
- `PROJECTBUDGET_GITHUB_TOKEN`: one read-only token for every private GitHub sync source, read from the environment or `.env` and never stored; a source's own token variable overrides it. The operations guide has the fine-grained token recipe and where the token goes for `uvx`, Docker and Compose (#30).
- A source's branch, path, token variable and CSV project id can be changed on the source page and with `PATCH /api/v1/sync/sources/{id}`; the source page says which token it will use (#29, #31).
- A sync source can be attached to an existing project (chosen on the Add source form or with `project_id` on the API). On the first apply, hand-entered requirements whose catalog item the manifest also names are adopted, keeping intent, sharing, notes and consumption; the plan lists adoptions as their own action and warns when a source with no project chosen would create a second project with the same name (#26, ADR-012).
- The Add a requirement form has a Kind field (consumable or reusable), and `POST /api/v1/requirements` takes `kind`, used only when the typed name creates a new catalog item; a list of tools no longer needs a trip to each item page to make it reusable (#18).
- Browser smoke tests (`tests/e2e`, Playwright, Chromium) that sign in, run the core journey, check destructive buttons ask first and fail on console errors, at desktop and phone width; CI runs them as "UI smoke (Playwright)".

### Changed

- Dates the app derives from an instant (today's date for form defaults and "Mark purchased", a manifest file's modification time, a commit time) are now the owner's local date in the workspace time zone, `PROJECTBUDGET_TIMEZONE` (an IANA name; default: the server's zone, UTC in the container), instead of the UTC date, which ran a day ahead in the evening. Timestamps on the sync runs and API tokens pages and in a synced project's banner are shown in that zone and labelled with it; dates the owner types are stored as written (#27, ADR-012).
- The purchases list has a "What" column (the first item, "and N more" when there are more lines) and a purchase page is headed by the same summary, so two "Mark purchased" clicks on one day can be told apart; the purchase date, estimate as-of, stock movement, received-on and balance-as-of inputs now default to today (#23).
- The backlog's money figures (line totals and "Still to buy") count only what is not yet on order, the same quantity the timeline buys, so the two pages no longer disagree by the value of open orders. A "Committed on order" tile shows the money on every open order at the purchases' prices, whether or not its item still shows, and a fully ordered row carries an "on order" badge and a 0.00 line total, with or without an estimate, until it is received (#25, ADR-012).
- Default backlog and timeline order within a priority tier is now by project (creation order) and then each project's own requirement order instead of alphabetical; a manifest sets that order to its file order on every apply, so the file's order is the purchase order (#34, ADR-012). The owner's explicit backlog order still wins.
- Review state now has a visible purpose: the catalog page says that confirming an item stops a manifest from changing its kind or unit, offers a one-click Confirm on each unreviewed row and a filter for unreviewed items (`GET /api/v1/catalog/items?review_state=unreviewed`), and the app confirms an item itself when the owner edits it, counts its stock or buys it. The item page's Review select is gone; saving the form, or "Confirm as is", confirms the item (#36, ADR-012).
- Receiving a purchase now records the price paid as a `purchase`-origin estimate, dated the day it arrived, so the current price follows what was actually paid; and a received stock movement says which purchase it came from ("Purchase 2026-11-09 from Tool Shop"), linked on the item page, with `purchase_id` and `purchase_line_id` on the movements API (#24).
- The backlog page, the README and the RFC's thesis now say what ADR-012 decided: several projects' lists in one priority queue against one budget, with consolidation per item as the mechanism that keeps the queue honest rather than the headline (#32).

### Fixed

- Applying a sync plan and changing the password now redirect to the source page and the account page instead of answering on the form's POST address, so a browser refresh re-reads the page rather than resubmitting, and the preview, apply and password addresses answer a plain visit (Back, Forward, a pasted link) with a redirect instead of a bare `Method Not Allowed` (#69).
- When a sync preview finds the content unchanged, the notice now names the run it matches the way the Runs table does, by date and revision, instead of quoting a content hash that never matched the revision column for a GitHub source (#68).
- A purchase's lines table, the sync preview plan and the projects list stack at phone width like the other tables, so the Received column and the Receive button of a purchase line are on screen at 320 px instead of behind a sideways scroll (#66).
- The backlog's "needs a price" table shows an "On order" column like the priced table, so an unpriced requirement that is already ordered no longer reads as if nothing were coming (#70).
- The budget page's "Last saved" time is shown in the workspace zone and labelled with it, like every other timestamp, instead of in UTC (#71).
- The Docker image now reports the version it was built with: the build handed it to a setuptools-scm variable that hatch-vcs ignores, so `/health` and `projectbudget version` in the container said `0.0.0`. A rebuild with a new version also no longer reuses the previous version's project wheel from the uv cache. CI builds the image twice with different versions and asserts both.
- The API tokens, price estimates, stock movements and budget override tables stack at phone width like the other tables, so the tokens page and an item page no longer scroll sideways at 320 px.
- A synced project's page now says which source it follows, when that source was last applied and at which revision, with a link to the source page; its Add a requirement form warns that hand-added rows stay manual, and such rows carry a "manual" badge. The requirement API gains `source_id` for the same distinction (#35).
- An exclusive reusable item now gets an "exclusive" chip on the backlog beside the existing "shared" one, and the project page's Sharing column always shows the stored value, greyed out for consumables with a note that sharing only matters for reusable items (#19).
- The timeline chart has a caption explaining that the optimistic balance can sit below the conservative one in a month when it affords a purchase sooner, and hovering a purchase bar lists the items bought that month (#22).
- The backlog's empty state on a fresh workspace says that no project has requirements yet and links to Projects, instead of claiming every requirement is covered by stock; when requirements exist but all are paused, cancelled or archived it says so (#20).
- The timeline no longer shows a red negative "opening balance" or says "blocked" when the only shortfall is the protected reserve and this month's contribution covers it. The first tile is now what is spendable (floored at zero) beside the reserve, the balance basis is one line under the tiles, "blocked" means the first month still cannot buy after its contribution, and the budget page says the contribution is counted in full this month and how to offset one that is already in the balance, with a negative current-month override (#21).
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
