# Operations guide

How to install, run, back up, restore and upgrade ProjectBudget. Everything here is the SQLite path unless a section says PostgreSQL.

## Install

| Path | Command | Data lives in |
| --- | --- | --- |
| `uvx` (no install) | `uvx projectbudget init --username you` then `uvx projectbudget run` | `~/.projectbudget/projectbudget.sqlite` |
| `pipx` | `pipx install projectbudget` then the same two commands | `~/.projectbudget/` |
| Docker | `docker run -d --name projectbudget -p 8000:8000 -v projectbudget-data:/data ghcr.io/sdeery14/projectbudget` then `docker exec -it projectbudget projectbudget init --username you` | the `projectbudget-data` volume |
| Compose with PostgreSQL | `cp .env.example .env`, set `POSTGRES_PASSWORD`, `docker compose up -d`, then `docker compose exec app projectbudget init --username you` | the `pgdata` volume |

`init` applies migrations, creates the single owner and fixes the workspace currency (`--currency EUR`; USD by default). There are no default credentials: until `init` has run the app serves only `/health`.

To try it without touching your data: `uvx projectbudget run --demo` creates a throwaway SQLite workspace in a temporary folder, seeds fictional data and prints the demo login.

## Configuration

Every setting is an environment variable with the `PROJECTBUDGET_` prefix, or a line in a `.env` file in the working directory. See `.env.example` for the full list. The ones that matter in production:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROJECTBUDGET_DATA_DIR` | `~/.projectbudget` | SQLite file, import directory and backups |
| `PROJECTBUDGET_DATABASE_URL` | SQLite in the data dir | `postgresql+psycopg://user@host:5432/db` for PostgreSQL |
| `PROJECTBUDGET_DATABASE_PASSWORD` | unset | database password kept out of the URL |
| `PROJECTBUDGET_HOST` / `PORT` | `127.0.0.1` / `8000` | bind address |
| `PROJECTBUDGET_SECURE_COOKIES` | `true` | keep on; browsers treat `localhost` as secure |
| `PROJECTBUDGET_SYNC_IMPORT_DIR` | `<data dir>/imports` | the only directory file and CSV sync sources may read |

## Behind a reverse proxy

Run one worker, terminate TLS at the proxy and pass the `Host` header through. The app checks the `Origin` header against `Host` (or `X-Forwarded-Host`) on every state-changing request, so the proxy must forward one of them. A Caddy example:

```
budget.example.net {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy forwards `Host` and `X-Forwarded-*` by default and obtains the certificate itself. With nginx, set `proxy_set_header Host $host;` and `proxy_set_header X-Forwarded-Proto $scheme;`.

## Backup

Three ways, from most to least complete:

1. **`projectbudget backup`** writes a consistent copy of the SQLite database with `VACUUM INTO` to `<data dir>/backups/projectbudget-<timestamp>.sqlite` (or `--output PATH`). Never copy the `.sqlite` file by hand while the app runs: under WAL the write-ahead log is a second file and a plain copy can be torn.
2. **`projectbudget export --output workspace.json`** writes the whole workspace as one JSON document: projects, catalog, requirements, prices, stock movements, purchases, the budget plan, backlog order and sync sources. It does not contain the owner account or API tokens. It moves cleanly between SQLite and PostgreSQL.
3. **PostgreSQL:** `pg_dump` against the direct connection string (not through a transaction-mode pooler).

Copy backups off the host. Both forms contain your private budget data; treat them like the database.

In the container: `docker exec projectbudget projectbudget backup --output /data/backups/manual.sqlite`, then `docker cp projectbudget:/data/backups/manual.sqlite .`.

## Restore drill

Do this once after installing and again before every upgrade, so you know it works before you need it.

**From a SQLite backup**

1. Stop the app.
2. Move the current database aside: `mv ~/.projectbudget/projectbudget.sqlite ~/.projectbudget/projectbudget.sqlite.old` (also remove any `-wal` and `-shm` files next to it).
3. Copy the backup into place under the original name.
4. `projectbudget db current` must print the latest revision; if the backup predates an upgrade, `projectbudget db upgrade` brings it forward.
5. Start the app and check the backlog and timeline look as expected.

**From a JSON export**

1. Run `projectbudget init --username you` on an empty database if needed (an export does not carry the owner).
2. `projectbudget import workspace.json` into the empty workspace, or `projectbudget import workspace.json --replace` to overwrite an existing one. Every id is preserved, so bookmarks and API ids keep working.
3. Check `/backlog` and `/timeline`. The import round trip is tested to be byte-equal for both views.

**PostgreSQL:** `pg_restore` or `psql < dump.sql` into an empty database, then `projectbudget db upgrade` and start the app.

## Upgrade

1. Take a backup (above).
2. `uvx` always runs the latest release; with `pipx`, `pipx upgrade projectbudget`; with Docker, `docker pull ghcr.io/sdeery14/projectbudget` and recreate the container (the entrypoint runs `db upgrade` before starting).
3. For `uvx` and `pipx`, run `projectbudget db upgrade` once, then `projectbudget run`. Migrations are forward-only in normal use; `projectbudget db downgrade -1` exists for development.

## Health and monitoring

`GET /health` needs no login and reports `{"status": "ok", "database": "ok", "version": "..."}`, or 503 with `"degraded"` when the database does not answer. The container's `HEALTHCHECK` uses it. There is no telemetry; the app makes no outbound request except GitHub sync when you trigger it.

## Sync sources on disk

File and CSV sources read only from the import directory. Put manifests there (or in subfolders) and name them by relative path when adding a source, for example `heater/project-budget.yaml`. The schema is `docs/manifest.schema.json`; the guide is `docs/manifest.md`.
