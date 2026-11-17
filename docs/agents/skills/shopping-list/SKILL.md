---
name: shopping-list
description: Build or update this project's shopping list as the project-budget.yaml manifest that ProjectBudget syncs. Use when the owner asks for a shopping list, parts list, bill of materials, what to buy, or to add, remove or reprioritise things the project needs.
---

# /shopping-list

Write the project's needs into `project-budget.yaml` at the repository root, in manifest v1, and validate it. Do not create lists anywhere else; ProjectBudget reads this file and nothing else.

The schema is <https://github.com/sdeery14/project-budget/blob/main/docs/manifest.schema.json> and the guide is <https://github.com/sdeery14/project-budget/blob/main/docs/manifest.md>. The shape:

```yaml
version: 1
project:
  id: garden-gate            # stable slug, required; never rename it
  name: Garden gate          # optional
items:
  - id: gate-hinge-set       # stable slug, unique in the file; never rename it
    name: Gate hinge set
    qty: 1
    unit: set                # default each
    priority: 1              # 1 most urgent .. 3, default 2
    estimate: { amount: "24.00", currency: USD }   # quoted decimal string, optional
  - id: post-level
    name: Post level
    qty: 1
    kind: reusable           # consumable (default) or reusable
    sharing: shared          # reusable only: shared (default) or exclusive
    catalog: post-level      # optional: reuse an existing catalog item by slug
```

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
- Unknown fields are errors.
- Two items may point at the same catalog item, but if they define its kind or unit differently the later one is rejected when the plan is made.
- An item that reuses an existing catalog item must carry the catalog's `kind` and `unit` (the defaults are `consumable` and `each`). A mismatch is not a validation error: it rejects that one item when the plan is made, so the requirement never arrives. A manifest may change kind or unit only on an item it created that the owner has not yet confirmed, edited, counted or bought.

## Reading the owner's data

If the ProjectBudget MCP server is configured, use its tools: `get_backlog`, `get_timeline`, `search_catalog` and `get_catalog_item` say what is owned, priced and on order, and `preview_sync` shows the plan this file would make. If a server is reachable and the owner has given you an API token instead, the JSON API under `/api/v1` has the same data; the OpenAPI document is at `/api/v1/openapi.json`. Otherwise ask the owner for the export JSON from the app's Data page: it holds every project, item, price, stock level, purchase and open order.
