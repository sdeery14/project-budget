# Architecture decision records

One file per decision, numbered in the order they were made. Each ADR has a Status, the Context that made the decision necessary, the Decision itself, and its Consequences. Accepted ADRs change only by a new ADR that supersedes them.

| ADR | Title | Status |
| --- | --- | --- |
| [001](001-database-sqlite-default-postgresql-supported.md) | Database: SQLite default, PostgreSQL supported | Accepted |
| [002](002-ui-jinja2-htmx-with-json-api-parity.md) | UI: Jinja2 + HTMX with JSON API parity | Accepted |
| [003](003-money-integer-minor-units-one-currency.md) | Money: integer minor units, one workspace currency | Accepted |
| [004](004-item-kind-and-consolidation.md) | Item kind and consolidation | Accepted |
| [005](005-auth-single-owner-principal-chokepoint.md) | Auth: single owner now, principal chokepoint for later | Accepted |
| [006](006-forecast-v1-semantics.md) | Forecast V1 semantics | Accepted |
| [007](007-sync-contract.md) | Sync contract | Accepted |
| [008](008-canonical-export-format.md) | Canonical export format | Accepted |
| [009](009-license-apache-2-no-cla.md) | License: Apache-2.0, no CLA | Accepted |
| [010](010-distribution.md) | Distribution | Accepted |
| [011](011-deferred-list-is-binding.md) | Deferred list is binding | Accepted |
| [012](012-per-project-lists-one-budget.md) | Per-project lists against one budget | Accepted |

The first eleven were accepted on 2026-11-07 with [RFC v3](../rfc/ProjectBudget-RFC-v3.md), which holds the full specification each ADR points into. New ADRs start from [000-template.md](000-template.md).
