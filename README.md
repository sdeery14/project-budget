# ProjectBudget

**Given everything my projects still need, what can I buy next, and when?**

ProjectBudget keeps several projects' shopping lists honest against one budget. Each project says what it still needs, as a manifest in its repository or a list you type in; the backlog puts all of it in one priority queue; the timeline schedules the purchases month by month against a discretionary budget you enter yourself. It is for makers, cyclists, homelabbers and DIYers running two or more projects at once, self-hosted as one process on a laptop, NAS or small VPS. SQLite by default, PostgreSQL supported, no telemetry, your data exports to one JSON file.

## Quickstart

```sh
# Try it on fictional data first (throwaway database, login printed on start)
uvx projectbudget run --demo

# Zero-infrastructure install: SQLite in ~/.projectbudget/
uvx projectbudget init --username you   # creates the owner; prompts for a password; --currency EUR to change the default USD
uvx projectbudget run                   # then sign in at http://127.0.0.1:8000/login

# Or a single container with SQLite on a volume
docker run -d --name projectbudget -p 8000:8000 -v projectbudget-data:/data ghcr.io/sdeery14/projectbudget
docker exec -it projectbudget projectbudget init --username you
```

PostgreSQL users: `cp .env.example .env`, set `POSTGRES_PASSWORD`, `docker compose up -d`, then `docker compose exec app projectbudget init --username you`. The [operations guide](docs/operations.md) covers configuration, running behind a reverse proxy, backups, the restore drill and upgrades.

There are no default credentials: the app serves only `/health` until `init` has run. The session cookie is `Secure` by default; browsers treat `localhost` as secure, so that only matters for plain HTTP on another host (`PROJECTBUDGET_SECURE_COOKIES=false` relaxes it on a trusted network).

## How it works

One loop. Projects say what they need, the budget says what there is, and the two read-only pages in the middle answer the question at the top of this file.

```text
 ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
 │ 1 Projects │──►│ 2 Budget   │──►│ 3 Backlog  │──►│ 4 Timeline │──►│ 5 Purchase │
 │ what each  │   │ what you   │   │ what to    │   │ when the   │   │ order,     │
 │ one needs  │   │ have       │   │ buy next   │   │ money is   │   │ receive,   │
 │            │   │            │   │            │   │ there      │   │ consume    │
 └────────────┘   └────────────┘   └────────────┘   └────────────┘   └─────┬──────┘
       ▲                                                                   │
       └──── manifests re-sync as projects change; stock lowers the need ◄─┘
```

1. **Add projects.** On the Projects page, type what a project needs: a name, a quantity, a priority. Or keep a `project-budget.yaml` next to the project, in a folder under the import directory or in a GitHub repository, add it as a sync source, preview the plan and apply it ([manifest guide](docs/manifest.md)). A synced project follows its manifest; a hand-made one is yours to edit.
2. **Set the budget.** On the Budget page, enter the balance set aside for projects, the date it was true, a protected reserve the timeline never spends below, and the monthly contribution.
3. **Read the backlog.** One queue across every project, priority first, with stock and open orders already subtracted and unpriced items flagged rather than summed. Price an item from its catalog page when the backlog says it needs one.
4. **Read the timeline.** The same queue laid against the budget month by month: for each item, the month it becomes affordable, and for each month, opening balance, contribution, purchases and closing balance.
5. **Buy.** Click "Mark purchased" on the timeline, or record the order on the Purchases page. Receive it when it arrives, which puts it in stock and records the price you paid, then consume it against the project when you use it. The backlog and timeline update on their own.

Then repeat: edit the manifest or the project as plans change, sync again, and the queue moves with it. Nothing is deleted by a sync; items that leave a manifest are archived and keep their purchase history.

## What it does

- **One backlog across projects.** Every project's active requirements in one queue: priority first, then project, then the project's own order, which for a synced project is the manifest's file order. A part that several projects need shows once: consumables add up, reusable tools count once for the largest single demand or per project when marked exclusive. Stock and on-order quantities are shown, so you see the shortfall and what is already coming.
- **Honest totals.** An item without a price is counted, flagged and never summed as zero. Totals are labelled lower bounds whenever anything is unpriced.
- **Explainable timeline.** Enter the balance you have set aside, the date it was true, a protected reserve and a monthly contribution. Every month shows opening balance, contribution, purchases and closing balance, in strict priority order with no clever reordering, with an optimistic and a conservative date per item. Guards tell you when something is not fundable, beyond the horizon, blocked because the balance stays below the reserve, or priced from a stale estimate.
- **Purchases and inventory.** Record what you ordered or bought line by line, receive it in parts, consume it against a project, or mark a timeline item purchased in one click. Spending visibly leaves the budget.
- **Catalog.** Items with stable slugs, kinds and units; price history per item; duplicates merged in one step with redirects so old links keep working.
- **Sync.** Keep a `project-budget.yaml` next to a project, in a folder under the import directory or in a GitHub repository, and pull it in: a dry-run plan first, then one-transaction apply. Items that leave the manifest are archived, never deleted; purchases, stock and the budget are never touched. See the [manifest guide](docs/manifest.md) and the [schema](docs/manifest.schema.json).
- **Export and import.** The whole workspace as one versioned JSON document, id for id, for backup, moving between SQLite and PostgreSQL, or seeding a demo. Plus `projectbudget backup` for a consistent SQLite copy.
- **JSON API.** Everything the pages do is available under `/api/v1` with bearer tokens you create on the API tokens page (read-only or read-write); the OpenAPI document is at `/api/v1/openapi.json` once signed in.

## Command line

| Command | Does |
| --- | --- |
| `projectbudget init --username NAME [--currency USD]` | apply migrations, create the owner, fix the currency |
| `projectbudget passwd --username NAME` | set a new password without the old one (forgotten password); signs every session out |
| `projectbudget run [--host] [--port] [--demo]` | start the single-worker server |
| `projectbudget export [--output PATH]` / `import PATH [--replace]` | the JSON workspace document |
| `projectbudget backup [--output PATH]` | consistent SQLite copy (`VACUUM INTO`) |
| `projectbudget reset [--yes]` | empty the workspace, keeping the owner, sessions, tokens and currency |
| `projectbudget sync SOURCE_ID [--dry-run] [--force]` | pull a manifest and apply its plan |
| `projectbudget manifest validate PATH [--project-id ID] [--format json]` | check a `project-budget.yaml` (or a CSV) with the sync parser, no server needed; exit 1 lists every problem with its line |
| `projectbudget db upgrade\|downgrade\|current` | migrations |

## Design and security

The design is settled in the [RFC v3](docs/rfc/ProjectBudget-RFC-v3.md) and recorded as [ADRs](docs/adr/). The [threat model](docs/threat-model.md) says what V1 protects and what it leaves out. Security reports go through [SECURITY.md](SECURITY.md), never public issues.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). One command sets up a dev environment: `uv sync --group dev && uv run pre-commit install`. Releases follow [docs/release.md](docs/release.md); changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
