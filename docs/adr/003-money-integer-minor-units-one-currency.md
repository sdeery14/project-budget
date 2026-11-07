# ADR-003: Money: integer minor units, one workspace currency

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §4 (Invariants), §6 (Money in the manifest)

## Context

Budget software that drifts by a cent loses trust. Floats drift; DECIMAL columns are not available in the same form on SQLite and PostgreSQL (ADR-001). The target user has one currency.

## Decision

- `Money(amount: int, currency: str)`: the amount is an integer in the currency's minor unit (cents for USD); the currency is an ISO 4217 code.
- The workspace currency is set at `projectbudget init`. Writes in any other currency are rejected. The fixture and documentation use USD.
- At every text boundary (manifest, CSV, API, export) money is a decimal string or an integer minor-unit field, never a float. The manifest JSON Schema rejects YAML numbers for `amount`; the parser uses `Decimal` and rejects more fractional digits than the currency allows.
- Arithmetic lives in the domain layer and is property-tested with Hypothesis.

## Consequences

- No foreign exchange, no rounding drift, works identically on both databases.
- Multi-currency is on the "Not in V1" list and needs a new ADR (ADR-011).
