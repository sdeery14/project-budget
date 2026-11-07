# ADR-002: UI: Jinja2 + HTMX with JSON API parity

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §3 (Routers), §7 (Security headers)

## Context

The UI is forms, tables and one timeline chart. It must run on cheap hosts, be testable without a browser, and be maintainable by one person. It must also be usable on a phone in a store. A client-side framework would add a build step and a second codebase to keep in sync with the API.

## Decision

- Server-rendered pages with Jinja2 and HTMX fragments. One vendored, no-build JavaScript chart library for the timeline.
- Every state-changing UI action has a JSON twin under `/api/v1`. Both route families call the same service functions with the same request models. Neither contains business logic. A test asserts parity.
- **No in-process state.** Login sessions and login rate-limit buckets are rows in the database. The app runs one worker by default; more are safe because nothing lives in process memory.
- HTMX 2 is pinned. Templates never use `hx-on` attributes or `js:`-prefixed values, so a Content Security Policy with no inline scripts and no `unsafe-eval` holds. A test greps the templates for both.
- Exit path: a single-page application can replace the templates without touching the service layer.

## Consequences

- No WebSockets. Testable end to end with an HTTP client; a browser smoke test runs nightly, not per PR.
- Slightly less fluid grid editing than a client-side framework would give.
- Sessions survive restarts and multiple workers at the cost of one small table and one query per request.
