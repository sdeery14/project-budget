# ProjectBudget

**Given everything my projects still need, what can I buy next, and when?**

ProjectBudget consolidates the parts and tools your projects still need into one honest shopping backlog, then schedules purchases month by month against a discretionary budget you enter yourself. It is for makers, cyclists, homelabbers and DIYers running two or more projects with overlapping parts, self-hosted as one process on a laptop, NAS or small VPS.

> **Status: pre-alpha.** Nothing runs yet. The design is approved and implementation follows the roadmap below. Star or watch the repo if you want to know when the first release lands.

## What it will do (V1)

- **Consolidated backlog.** Requirements from every project roll up per catalog item. Consumables add up; reusable tools count once for the largest single demand. Owned and on-order quantities are subtracted so you only see the shortfall.
- **Honest totals.** An item without a price is counted, flagged and never summed as zero. Totals are labeled lower bounds whenever anything is unpriced.
- **Explainable forecast.** Enter your balance, reserve and monthly contribution. Get a month-by-month purchase timeline where every month shows opening balance, contribution, purchases and closing balance. Strict priority order, no clever reordering, so you can check it by eye.
- **Purchases and inventory.** Record what you bought, receive it in parts, and consume it against a project. Spending visibly leaves the budget.
- **Optional sync.** Keep a `project-budget.yaml` next to your project, locally or in a GitHub repo, and pull it in with a dry-run diff. Sync never touches purchases, inventory or budget.
- **Yours to keep.** SQLite by default, PostgreSQL supported. Versioned JSON export and import of the whole workspace. No telemetry.

## Quickstart (placeholders until the first release)

```sh
# Zero-infrastructure install, SQLite in ~/.projectbudget/
uvx projectbudget init --username you   # creates the owner; prompts for a password; --currency EUR to change the default USD
uvx projectbudget run                   # then sign in at http://127.0.0.1:8000/login

# Or a single container with SQLite on a volume
docker run -d --name projectbudget -p 8000:8000 -v projectbudget-data:/data ghcr.io/sdeery14/projectbudget
docker exec -it projectbudget projectbudget init --username you

# Or try it with fictional data
uvx projectbudget run --demo
```

There are no default credentials: the app serves only `/health` until `init` has run. `init` also fixes the workspace currency (USD unless `--currency` says otherwise); every price is an exact amount in that one currency (ADR-003). Behind a reverse proxy, terminate TLS there and pass the `Host` header through; the session cookie is `Secure` by default (`PROJECTBUDGET_SECURE_COOKIES=false` relaxes that for plain HTTP on a trusted network). API tokens for scripts are created on the **API tokens** page and sent as `Authorization: Bearer ...` to `/api/v1`.

## Roadmap

| Milestone | Delivers |
| --- | --- |
| M0 Skeleton | Repo, ADRs, CI on both databases, app factory, migrations, CLI, Docker image, owner auth |
| M1 Backlog | Projects, catalog, requirements, prices, consolidation |
| M2 Forecast | Budget plan and the explainable timeline |
| M3 Purchases and inventory | Purchases, partial receipt, consumption, export and import |
| M4 Sync | Manifest schema, file, CSV and GitHub sources, dry-run and apply |
| M5 Release | Docs, demo mode, backup drill, PyPI and GHCR release |

The full design, acceptance criteria and PR plan are in the [RFC v3](docs/rfc/ProjectBudget-RFC-v3.md). Decisions are recorded as [ADRs](docs/adr/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). One command sets up a dev environment: `uv sync --group dev && uv run pre-commit install`.

Security reports go through [SECURITY.md](SECURITY.md), never public issues.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
