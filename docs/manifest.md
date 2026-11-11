# The manifest: `project-budget.yaml`

A manifest describes what one project needs. Keep it next to the project, in a folder under the import directory or in a GitHub repository, add it as a sync source, and pull it in whenever it changes. Sync shows what it would do before it does it, applies in one step, and never touches purchases, stock or the budget (RFC v3 §6, ADR-007). The machine-readable schema is `docs/manifest.schema.json`.

## Example

```yaml
version: 1
project:
  id: hcalory-heater          # stable slug, required; never rename it
  name: Diesel heater install  # optional, shown in the UI; defaults to the id
items:
  - id: fuel-line-6mm          # stable within the project, required
    name: 6 mm fuel line
    qty: 3
    unit: m                    # default: each
    estimate: { amount: "12.50", currency: USD }   # amount is a quoted decimal string
  - id: clamps
    name: Hose clamps
    qty: 6
    kind: reusable             # consumable (default) or reusable
    priority: 1                # 1 most urgent .. 3, default 2
    sharing: exclusive         # reusable only: shared (default) or exclusive
    catalog: hose-clamp        # optional: an existing workspace catalog slug
    notes: stainless
```

Rules the parser enforces, each reported with its line number: ids are lower-case slugs; `qty` is a whole number above zero; `amount` is a quoted string such as `"12.50"` (a bare YAML number is rejected, so a float can never slip into money) or `amount_minor` is an integer; the currency, when given, must be the workspace currency; unknown fields are errors; duplicate ids reject the whole file.

## CSV

A CSV source has fixed columns `id,name,qty,unit,kind,priority,sharing,catalog,estimate` and no project block; the project id is set on the source. Blank cells take the defaults above; `estimate` is a decimal string in the workspace currency.

## What sync does

| In the manifest | Result |
| --- | --- |
| Same content as the last applied run | No-op, recorded |
| New item id | A requirement; a new unreviewed catalog item unless `catalog` names an existing one (or the name's slug already exists) |
| Existing id with changed qty or priority | The requirement is updated |
| Items in a different order | The project's requirements take the file's order, and so does the backlog's default order |
| Hand-entered requirement for the same catalog item, in the project the source is attached to | Adopted on the first apply: it becomes the source's, with the manifest's qty, priority and order; intent, sharing, notes and consumption stay |
| Manifest project name matches an existing project that is not synced, and no project was chosen | The plan warns; applying still creates a new project |
| Project name or id changed in the file (or the CSV project id on the source) | The plan shows a `project` row; applying renames the synced project |
| Existing id with a changed name | The catalog item is renamed while the source owns it (see below); the id and slug never change |
| Id missing from the file | The requirement is archived with reason `removed_from_source`; purchases and consumption stay |
| Id back again | The same requirement is reactivated |
| Same name under a new id | A new requirement plus an archive of the old one; the plan flags the likely rename |
| Schema error anywhere | Whole file rejected with line-level messages; nothing applied |
| Kind or unit that differs on an item the source does not own | That item is rejected; everything else applies |
| Two items in one file defining the same catalog item differently | The later one is rejected |
| Estimate in another currency | Only the estimate is rejected; the item still applies |

**Who owns which field.** The manifest owns `name`, `qty`, `priority`, the initial `sharing`, the estimate (recorded with origin `manifest` and dated by the file's mtime or the commit date, so a newer manual price keeps winning), and the order of the items within the project. It owns `kind` and `unit` only for a catalog item it created that is still unreviewed and that nothing else references. You own the catalog mapping, intent (active, paused, cancelled, archived), the sharing override, notes and the workspace backlog order; the manifest never changes them. Archiving something yourself keeps it archived even if the manifest still lists it.

**Order matters.** List items in the order you intend to buy them. Within a priority tier the backlog and the timeline follow it (ADR-012), so a part comes after the thing it attaches to when the file says so. Moving a row on the Backlog page overrides the default order for the whole workspace until you move again.

**Attaching to a project you built by hand.** Choose the project when adding the source (the Add source form, or `project_id` on the API). The source then syncs into that project instead of creating one: on the first apply, each hand-entered requirement whose catalog item the manifest also names is adopted, and everything else in the project stays manual and untouched. A manifest item whose catalog item is not in the project is created as usual. A project can follow only one source.

**Detach** removes the source and its run history; the project and its requirements stay and become manual.

## Sources

| Kind | Locator | Revision | Estimate date |
| --- | --- | --- | --- |
| `file` | path relative to the import directory, e.g. `heater/project-budget.yaml` | SHA-256 of the content | file mtime |
| `csv` | same, plus the project id on the source | SHA-256 of the content | file mtime |
| `github` | `owner/repo`, optional branch, path (default `project-budget.yaml`) | latest commit SHA touching the file | that commit's date |

GitHub sources talk only to `api.github.com`. Public repositories need nothing. For private ones, set one fine-grained read-only token as `PROJECTBUDGET_GITHUB_TOKEN` on the server and every GitHub source uses it; a source may name its own token variable instead, for a repository that needs a different token. The token itself is never stored. The recipe is in `docs/operations.md`, section "GitHub tokens". Branch, path and token variable can be changed on the source page; kind and locator cannot, since they are the source's identity.

From the command line: `projectbudget sync SOURCE_ID --dry-run` prints the plan, `projectbudget sync SOURCE_ID` applies it, `--force` plans even when the content is unchanged.
