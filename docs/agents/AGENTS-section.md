## Shopping list: `project-budget.yaml`

This project's shopping list lives in `project-budget.yaml` at the repository root. ProjectBudget syncs it: the owner previews the plan and applies it, and the backlog and timeline update. When you change what the project needs, change this file; do not write lists anywhere else.

The format is manifest v1. The machine-readable schema is `docs/manifest.schema.json` in the ProjectBudget repository and the guide is `docs/manifest.md`. Example:

```yaml
version: 1
project:
  id: garden-gate            # stable slug, required; never rename it
  name: Garden gate          # optional, shown in the UI
items:
  - id: gate-hinge-set       # stable within the project, required; never rename it
    name: Gate hinge set
    qty: 1
    unit: set                # default: each
    priority: 1              # 1 most urgent .. 3, default 2
    estimate: { amount: "24.00", currency: USD }   # amount is a quoted decimal string
  - id: carriage-bolts
    name: M8 carriage bolt 80 mm
    qty: 12
    catalog: m8-carriage-bolt-80   # optional: reuse an existing catalog item by its slug
  - id: post-level
    name: Post level
    qty: 1
    kind: reusable           # consumable (default) or reusable
    sharing: shared          # reusable only: shared (default) or exclusive
    notes: borrow first if a neighbour has one
```

Rules the parser enforces, each reported with its line number:

- `version` is `1`. `project.id` and every item `id` are slugs: lower-case ASCII letters, digits and single hyphens, 1 to 100 characters. They are identities, so never rename one; rename the `name` instead.
- Every item id is unique in the file. Two items for the same catalog item are rejected.
- `qty` is a whole number of at least 1. `priority` is 1, 2 or 3 (default 2); `kind` is `consumable` (default) or `reusable`; `sharing` is `shared` (default) or `exclusive` and only matters for reusable items; `unit` defaults to `each` and is at most 30 characters; `name` is at most 200 characters.
- `estimate.amount` is a quoted decimal string with at most two decimals, such as `"12.50"`. A bare YAML number is rejected, so a float can never reach the money code. Leave `estimate` out rather than invent a price; the owner prices items in the app and purchases record the real price.
- Unknown fields are errors. `kind` and `unit` are read only when the item is new to the catalog; an existing item keeps its own.
- The order of `items` is the purchase order within a priority. List things in the order they should be bought.

Before you stop, check the file:

```sh
uvx projectbudget manifest validate project-budget.yaml
```

Exit code 0 means valid; otherwise every problem is printed as `project-budget.yaml:LINE: message`. Fix them all. Do not remove items to make the file pass; an item that leaves the manifest is archived in the app, so a removal is a decision for the owner.

How it is used: the owner adds this repository as a sync source in ProjectBudget, clicks "Fetch and preview", reads the plan and applies it. If you cannot reach the owner's ProjectBudget server and need to know what is already owned, priced or on order, ask for the export JSON from the app's Data page; it holds the whole workspace.
