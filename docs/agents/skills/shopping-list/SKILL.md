---
name: shopping-list
description: Build or update this project's shopping list as the project-budget.yaml manifest that ProjectBudget syncs. Use when the owner asks for a shopping list, parts list, bill of materials, what to buy, or to add, remove or reprioritise things the project needs.
---

# /shopping-list

Write the project's needs into `project-budget.yaml` at the repository root, in manifest v1, and validate it. Do not create lists anywhere else; ProjectBudget reads this file and nothing else.

## Procedure

1. Read what the project is: the README, open issues, any plan or design notes, and the current `project-budget.yaml` if there is one. Work out what has to be bought, in which quantities, and what order the work needs them in.
2. Draft the list. One item per thing to buy. For each: a stable `id` (lower-case slug, never renamed later), a `name`, a `qty` of at least 1, and only when they differ from the defaults a `unit` (default `each`), a `priority` (1 most urgent, 2 default, 3 later), a `kind` (`consumable` default, `reusable` for tools), and for reusable items a `sharing` (`shared` default, `exclusive` if it must stay with this project). Add `notes` for anything the owner should know when buying.
3. Reuse before you create. If the owner's catalog already has the item, set `catalog:` to its slug so the app does not create a duplicate. Ask the owner or read the export JSON when you are not sure.
4. Prices: leave `estimate` out unless the owner gave a price or a definite list price is in the notes. When you do give one, it is `estimate: { amount: "12.50", currency: USD }` with the amount as a quoted decimal string. Never a bare number.
5. Order the items as they should be bought within each priority; the file order is the purchase order.
6. Write or edit the file, keeping existing ids unchanged. Removing an item archives it in the app, so remove only when the owner said so.
7. Validate:

   ```sh
   uvx projectbudget manifest validate project-budget.yaml
   ```

   Exit 0 means valid. Otherwise every problem is printed with its line; fix them all and run it again.
8. Show the owner the diff and a one-paragraph summary: what was added, changed or removed and why, and anything you were unsure about (a quantity, whether an item already exists in the catalog). Then stop; the owner previews and applies the sync in ProjectBudget.

## Rules the parser enforces

- `version: 1`; `project.id` and every item `id` are slugs (lower-case letters, digits, single hyphens, 1 to 100 characters) and unique within the file.
- `qty` is a whole number of at least 1; `priority` is 1 to 3; `name` is at most 200 characters; `unit` at most 30.
- `estimate.amount` is a quoted decimal string with at most two decimals; a YAML number is rejected.
- Unknown fields are errors. `kind` and `unit` only apply when the item is new to the catalog.

## Reading the owner's data

If a ProjectBudget server is reachable and the owner has given you an API token, the JSON API under `/api/v1` has the backlog, timeline, catalog and purchases; the OpenAPI document is at `/api/v1/openapi.json`. Otherwise ask the owner for the export JSON from the app's Data page: it holds every project, item, price, stock level, purchase and open order.
