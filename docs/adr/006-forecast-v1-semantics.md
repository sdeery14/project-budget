# ADR-006: Forecast V1 semantics

**Status:** Accepted
**Date:** 2026-11-07
**Implements:** RFC v3 §5 (Forecast semantics), Milestone 2

## Context

The forecast is the product thesis: what can I buy next, and when. It must be explainable by eye, deterministic, and honest about what it does not know. It must also move when the user spends money, or the timeline becomes fiction after the first purchase.

## Decision

- The forecast is a pure, deterministic function: `(BudgetPlan, backlog, purchases_since_as_of, today) → Schedule`. It touches no database. Every rule is a golden test.
- `BudgetPlan` carries `balance_as_of`; `earmarked_funds` is the balance on that date. Editing the balance resets `balance_as_of` to today.
- Month 1 opening balance = earmarked funds − protected reserve − Σ totals of purchases in `ordered` or `received` status dated on or after `balance_as_of`. Later months open with the previous closing.
- Each month: add the monthly contribution (month 1 receives the full contribution regardless of the day), apply labeled signed overrides, then buy from the head of the queue while it fits. Record opening, contribution, overrides, purchases and closing; that record is the explanation shown in the UI.
- Purchase unit: one backlog row is one indivisible purchase of net shortfall (gross shortfall − on-order, floored at zero) × latest unit estimate × (1 + contingency %). Rows with net shortfall 0 do not appear in the timeline.
- Queue order: row priority ascending, then the workspace backlog order. Strict; an item is never skipped because a cheaper one behind it would fit.
- Unpriced items go to a "needs a price" bucket, counted and never summed. Totals are labeled lower bounds. Shared reusable items are scheduled once; per-project views never sum to a grand total.
- Guards: "Not fundable at current inputs", "Beyond horizon" (default 36 months), stale-estimate flag at 90 days, blocked timeline while the balance is negative, "Balance may be stale" at 60 days, unpriced banner. *(#21: "blocked" is read as "the first month's balance after its contribution is still below zero"; a negative opening that the month's own contribution covers is not blocked, and the UI shows the opening floored at zero as "spendable" with the uncovered part of the reserve stated separately. The arithmetic is unchanged. A balance that already includes this month's contribution is offset with a negative current-month override; the budget page says so.)*
- Contingency % yields two schedules, optimistic and conservative; both dates are shown per item. The page carries: "Planning projection from your inputs; not financial advice."

## Consequences

- Explainable by construction. Spending visibly reduces the plan.
- No optimization, reordering or partial buys. A "fill gaps" toggle and scenario comparison are deferred (ADR-011).
