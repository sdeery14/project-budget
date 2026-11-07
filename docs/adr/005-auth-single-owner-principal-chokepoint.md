# ADR-005: Auth: single owner now, principal chokepoint for later

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §3 (Services), §7 (Security requirements)

## Context

V1 protects one owner's private budget data on an instance that may be reachable from the internet. Multi-user and multi-workspace are deferred, but the design must not make them expensive later.

## Decision

- One owner, created by `projectbudget init`. No default credentials; the app refuses to serve until an owner exists.
- Passwords hashed with Argon2id. Session cookies with a random id and a session row in the database: HttpOnly, Secure (configurable off for localhost), SameSite=Lax, idle timeout, rotated on login.
- CSRF: Origin/Referer check on every state-changing request plus a per-session token in HTMX forms. Login rate limit with database-backed buckets and constant-time comparison.
- API tokens: random, shown once, stored as SHA-256 hashes, revocable, flagged read-only or read-write. No finer scopes in V1.
- A `require_owner` dependency on every router except `/health`, `/login` and static assets. A test walks the route table to prove it.
- Every service function receives a `Principal`. In V1 it is always the owner. There is no Workspace entity.
- Migration to multi-user: add `workspace_id` to root tables with a single backfilled value and enforce filtering in the service layer, which is already the only path to the database.

## Consequences

- No premature tenancy. Authorization has exactly one place to live.
- Security headers, import limits, secret handling and backup drills are release blockers listed in RFC v3 §7 and tracked by named tests.
