# ADR-001: Database: SQLite default, PostgreSQL supported

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §3 (Architecture, Distribution), §8 (Testing)

## Context

Self-hosters are hobbyists running one process on a laptop, NAS or small VPS. They should not need Docker Compose or a database server to try the product, and a backup should be something they can understand. A hosted maintainer instance may come later and would want PostgreSQL. Money is stored as integer minor units (ADR-003), so DECIMAL columns are not a reason to require PostgreSQL.

## Decision

- SQLite is the default database. PostgreSQL is fully supported. Both run in CI for every PR.
- SQLAlchemy 2.0 against the **dialect-neutral subset**: no JSONB, no arrays, no partial indexes, no PostgreSQL enums. UUIDs are stored as text. Money is an integer. Enumerations are short text columns with a check constraint.
- Alembic migrations use batch mode so they apply on SQLite.
- The SQLite connect hook sets `PRAGMA foreign_keys=ON`, `journal_mode=WAL` and a busy timeout of 5 seconds on every connection. The test engine uses the same hook, so foreign-key violations fail on both backends.
- Backups: `projectbudget backup` runs `VACUUM INTO` for SQLite; `pg_dump` for PostgreSQL; `projectbudget export` (ADR-008) works for both. A plain copy of the SQLite file is documented as unsafe under WAL.

## Consequences

- Trivially simple self-host: one file, zero infrastructure.
- A small feature tax on PostgreSQL: no PostgreSQL-only types or indexes until a hosted offering justifies a new ADR.
- There is no SQLite-only mode either; anything that works on one backend and not the other is a bug.
