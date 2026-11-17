# Working on ProjectBudget

ProjectBudget schedules several projects' shopping lists against one discretionary budget: per-project requirements, one priority queue (the backlog), a month-by-month timeline, purchases and stock. One Python process, FastAPI + Jinja2/htmx pages with a JSON API twin, SQLite by default and PostgreSQL supported.

The specification is [RFC v3](docs/rfc/ProjectBudget-RFC-v3.md) and the decisions that bind it are the [ADRs](docs/adr/README.md). [CONTRIBUTING.md](CONTRIBUTING.md) holds the layer rules, conventions and the notes per subsystem; read it before changing code. The deferred list in the RFC is binding (ADR-011): nothing under "Not in V1" moves earlier without a new ADR.

## Commands

```sh
uv sync --group dev --group e2e
uv run ruff check --fix . && uv run ruff format .
uv run mypy
uv run pytest                 # SQLite; set PROJECTBUDGET_TEST_DATABASE_URL for PostgreSQL
uv run pytest tests/e2e       # Chromium smoke tests, desktop and 320 px; not part of a plain run
```

CI runs all of these on both databases. A PR merges only when every check is green and every review thread is resolved.

## Code review guidelines

Every PR names the ADR it implements under "ADRs implemented". Review the change against that ADR and the RFC section it points into, not against general preference. A change that goes beyond what the named ADR decided, or that touches something the deferred list reserves, is a finding even when the code is good.

Report findings at these severities, and include P2 findings in GitHub reviews: this project wants them.

- **P1, blocks the merge.** Wrong results or lost data; money handled as a float or rounded outside `domain/money.py` (ADR-003); a write that skips `guards.require_write` or a route without `require_owner` (ADR-005); a state-changing page action with no JSON twin, or a twin that behaves differently (ADR-002); a check-then-write that does not hold the item lock (`catalog.lock_item`) when a concurrent consumption or receipt could cross it; SQL that works on one database only (ADR-001); a manifest-owned field changed by the app for a synced row (ADR-007); a secret, a real name or real data in code, fixtures, tests or screenshots.
- **P2, fix before merge.** A user-visible defect in the page or API: an error the owner cannot see (htmx only swaps the statuses listed in `base.html`), a form field that cannot be cleared or omitted the way the form implies, a broken layout at 320 px, a missing confirmation on a destructive button; a behaviour change without a test, or a test that passes without the change; a `CHANGELOG.md` entry missing for a user-visible change; a docstring or page copy that now says the wrong thing.
- **P3, mention briefly.** Naming, duplication, a clearer way to write the same thing.

Do not report what the tooling already enforces: ruff and ruff format, `mypy --strict`, import order, line length, the template-safety and route-protection tests.

Each finding is one thread at the line it concerns: what goes wrong, the concrete input or sequence that shows it, and the change that fixes it. Say when a finding is a judgment call. If the PR is sound, say so in one line rather than listing P3s to fill the review.

## Making changes

- Services take the session factory, a `Principal` and a frozen command; routers and the CLI never touch ORM rows. Domain code imports no SQLAlchemy and does no I/O.
- Take `catalog.lock_item` before any read a decision depends on when stock can change underneath it.
- Every page form has a JSON twin under `/api/v1`; both call the same service function.
- Templates: no `hx-on`, `js:`, inline `<script>` or `style=`. The CSP forbids them and a test greps for them.
- Any change to a template or `app.css` is looked at in a browser at desktop and 320 px, and the browser tests under `tests/e2e` are extended or at least run.
- Tests, fixtures and screenshots use the canonical fictional workspace in `src/projectbudget/fixtures/canonical.py` only.
- One line under Unreleased in `CHANGELOG.md` for every user-visible change, with the issue number.
- Branch from `main`, one PR per issue, conventional-commit title (it becomes the squash commit), the PR body names the ADR.
