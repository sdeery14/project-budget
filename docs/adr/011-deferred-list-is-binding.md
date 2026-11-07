# ADR-011: Deferred list is binding

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §2 (Not in V1), §10 (Later)

## Context

A solo maintainer with a six-milestone roadmap cannot absorb scope that arrives one reasonable request at a time. The RFC already lists what V1 excludes; the list only helps if moving something off it has a visible cost.

## Decision

- Everything under "Not in V1" in RFC v3 §2 requires a new ADR before it moves earlier. The ADR names what it displaces or delays.
- Items on the list as of this ADR: GitHub OAuth or GitHub App, webhooks, multi-user or multi-workspace, API token scopes beyond read/write, refunds, requirement dependency graph, unit conversion, multi-currency, scenario comparison, background job queue, price scraping, bank sync, notifications, hosted maintainer instance, mobile app.
- Also deferred: the hosted maintainer instance and the full threat model (a two-page draft lands in Milestone 5).

## Consequences

- Scope creep has a cost everyone can see.
- Reviews can decline a feature by pointing at this ADR instead of relitigating the roadmap.
