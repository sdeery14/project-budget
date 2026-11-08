# Contributing

Thanks for helping. The design is settled in the [RFC v3](docs/rfc/ProjectBudget-RFC-v3.md) and the [ADRs](docs/adr/); most contributions implement a row of the RFC's roadmap or PR table.

## One-command setup

```sh
uv sync --group dev && uv run pre-commit install
```

That installs Python 3.12 if needed, creates `.venv`, installs the dev tools, and wires the pre-commit hooks. Nothing else is required for the SQLite path. For the PostgreSQL path, point `PROJECTBUDGET_TEST_DATABASE_URL` at a server, for example:

```sh
docker run --rm -d -p 5432:5432 -e POSTGRES_USER=projectbudget -e POSTGRES_PASSWORD=projectbudget -e POSTGRES_DB=projectbudget_test postgres:16
export PROJECTBUDGET_TEST_DATABASE_URL=postgresql+psycopg://projectbudget:projectbudget@localhost:5432/projectbudget_test
```

## Everyday commands

| Task | Command |
| --- | --- |
| Lint and format | `uv run ruff check --fix . && uv run ruff format .` |
| Types | `uv run mypy` |
| Tests | `uv run pytest` |
| Everything pre-commit checks | `uv run pre-commit run --all-files` |
| Dependency audit | `uv run pip-audit` |

CI runs the same commands on both SQLite and PostgreSQL. A PR merges only when both are green.

## Running the app locally

```sh
uv run projectbudget init --username you   # applies migrations, creates the owner (prompts for a password), sets the currency (--currency, default USD)
uv run projectbudget run                   # http://127.0.0.1:8000/login
```

Point `PROJECTBUDGET_DATABASE_URL` (or `--database-url`) at PostgreSQL to use that path instead. `projectbudget db revision -m "..."` drafts a migration from the models with the next sequential id; `db current`, `db upgrade` and `db downgrade` complete the set. The container runs `db upgrade` and then `run` on every start; create its owner with `docker exec -it <container> projectbudget init --username you`.

Auth notes for anyone touching routers (ADR-005): every router except `/health`, `/login` and `/static` depends on `require_owner`, and `tests/test_routes_require_owner.py` walks the route table to prove it. State-changing requests need a same-origin `Origin` (or `Referer`) header and, when authenticated by cookie, the per-session CSRF token: the base layout puts it in an `X-CSRF-Token` header on every HTMX request, and plain forms carry a hidden `csrf_token` field. Templates must not use `hx-on`, `js:`, inline `<script>` or `style=` attributes; the CSP forbids them and `tests/test_templates_safe.py` greps for them. htmx is vendored under `src/projectbudget/web/static/vendor/htmx/` with its version and checksum.

Service-layer notes (RFC v3 §3, ADR-005): every function in `src/projectbudget/services/` takes the session factory and a `Principal` first, then a frozen command dataclass, and returns a frozen info dataclass; routers and the CLI never touch ORM rows. Writes call `guards.require_write`, so a read-only API token is refused here even if a router forgot. Domain validation (`domain/catalog.py`, `domain/money.py`) runs before a transaction opens; anything that needs the database (slug taken, item missing, unit mismatch) is checked inside the one transaction that performs the write. Enumerations are `StrEnum`s stored as plain strings with `CHECK` constraints, never native enums (ADR-001).

Router notes (ADR-002): HTML pages live in `src/projectbudget/web/pages/` and their JSON twins in `src/projectbudget/api/`; both call the same service functions and contain no business logic. Request and response models for the API are in `api/schemas.py`; money crosses that boundary as a decimal string plus an integer minor-unit field, never a float. User-caused service errors are grouped in `services/errors.py`: validation errors answer 422, conflicts with existing state answer 409, missing records 404, read-only tokens 403, wired once in `web/errors.py`. Pages catch the same group to re-render the form with the message. HTMX fragments are returned for requests carrying `HX-Request`; the htmx config in `base.html` swaps 422 responses so an error row replaces the row that caused it.

The canonical fictional workspace is `src/projectbudget/fixtures/canonical.py`; tests, acceptance checks and the demo seed from it.

## Conventions

- **Branch from `main`, squash-merge back.** The PR title becomes the commit message, so write it as a [conventional commit](https://www.conventionalcommits.org/): `feat(domain): add Money`, `fix(sync): ...`, `docs: ...`, `chore(ci): ...`.
- **Every PR names the ADR it implements.** The PR template asks for it. Reviews check the change against that ADR, not against general preference.
- **The deferred list is binding (ADR-011).** Anything under "Not in V1" in the RFC needs a new ADR before it moves earlier. Open the ADR as its own PR first.
- **Layers are rules, not suggestions.** Domain code imports no SQLAlchemy and does no I/O. Only the service layer opens a session or commits. Routers contain no business logic. Every state-changing UI action has a JSON twin.
- **Money is an integer in minor units.** Floats never touch money, including at text boundaries (ADR-003).
- **Both databases, always.** Stay inside the dialect-neutral subset (ADR-001). If something works on PostgreSQL only, it is a bug.
- **Strict typing.** `mypy --strict` passes on `src` and `tests`. Relax a module only with a comment saying why.
- **No real data.** Fixtures, screenshots, issues and CI logs use the canonical fictional workspace only.

## Writing an ADR

Copy `docs/adr/000-template.md` to the next number, fill in Context, Decision and Consequences, set Status to Proposed, and open a PR. The ADR is Accepted when that PR merges.

## Releasing

Versions come from git tags. Tagging `vX.Y.Z` on `main` publishes the PyPI package and the GHCR image (ADR-010). The release checklist lives in the RFC's Milestone 5 and includes a backup restore drill.
