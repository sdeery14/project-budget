# ADR-008: Canonical export format

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §2 (acceptance test 5), §8 (Fixtures), Milestone 3

## Context

Users need a backup they can read, a way to move between SQLite and PostgreSQL, and a demo that seeds itself. Tests need a canonical fixture. One format serving all four avoids four serializers.

## Decision

- A versioned JSON document of the whole workspace, written by `projectbudget export` and read by `projectbudget import`.
- Used for backup, SQLite↔PostgreSQL migration, demo seeding (`run --demo`) and the canonical test fixture `tests/fixtures/workspace.json`.
- Import preserves every id. The round-trip test (export → wipe → import) compares backlog and forecast JSON byte for byte under a frozen clock.
- Every entity must round-trip; adding an entity means adding it to the serializer and the test.

## Consequences

- One serializer to maintain. The fixture, the demo and the backup format cannot drift apart.
- Schema changes bump the document version and keep an import path from the previous version.
