# ADR-010: Distribution

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §3 (Distribution, Hosting notes), §2 (acceptance test 8)

## Context

Most users will never run a database server. Some run a NAS with Docker. A few already run PostgreSQL. A second person must be able to self-host from the README alone.

## Decision

- A PyPI package runnable with `uvx projectbudget` or `pipx install projectbudget`, SQLite in `~/.projectbudget/` by default.
- A single container image on GHCR with SQLite on a mounted volume.
- An optional Docker Compose file with PostgreSQL.
- The container entrypoint runs `projectbudget db upgrade` and then `projectbudget run` with one worker. There are no in-app concurrent migrations.
- The version comes from the git tag. Tagging `vX.Y.Z` on `main` publishes the package and the image.
- Hosting notes document a TLS reverse proxy, `VACUUM INTO` or export for backups, and direct-connection strings for migrations behind a transaction-mode pooler.

## Consequences

- Zero-infrastructure install for most users; a clear path for NAS and PostgreSQL users.
- A maintainer-run hosted instance is deferred; `projectbudget run --demo` replaces it.
