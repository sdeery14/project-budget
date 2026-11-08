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
| Existing id with a changed name | The catalog item is renamed while the source owns it (see below); the id and slug never change |
| Id missing from the file | The requirement is archived with reason `removed_from_source`; purchases and consumption stay |
| Id back again | The same requirement is reactivated |
| Same name under a new id | A new requirement plus an archive of the old one; the plan flags the likely rename |
| Schema error anywhere | Whole file rejected with line-level messages; nothing applied |
| Kind or unit that differs on an item the source does not own | That item is rejected; everything else applies |
| Two items in one file defining the same catalog item differently | The later one is rejected |
| Estimate in another currency | Only the estimate is rejected; the item still applies |

**Who owns which field.** The manifest owns `name`, `qty`, `priority`, the initial `sharing`, and the estimate (recorded with origin `manifest` and dated by the file's mtime or the commit date, so a newer manual price keeps winning). It owns `kind` and `unit` only for a catalog item it created that is still unreviewed and that nothing else references. You own the catalog mapping, intent (active, paused, cancelled, archived), the sharing override, notes and ordering; the manifest never changes them. Archiving something yourself keeps it archived even if the manifest still lists it.

**Detach** removes the source and its run history; the project and its requirements stay and become manual.

## Sources

| Kind | Locator | Revision | Estimate date |
| --- | --- | --- | --- |
| `file` | path relative to the import directory, e.g. `heater/project-budget.yaml` | SHA-256 of the content | file mtime |
| `csv` | same, plus the project id on the source | SHA-256 of the content | file mtime |
| `github` | `owner/repo`, optional branch, path (default `project-budget.yaml`) | latest commit SHA touching the file | that commit's date |

GitHub sources talk only to `api.github.com`. Public repositories need nothing; for a private one, put a fine-grained read-only token in an environment variable on the server and give the variable's name on the source. The token itself is never stored.

From the command line: `projectbudget sync SOURCE_ID --dry-run` prints the plan, `projectbudget sync SOURCE_ID` applies it, `--force` plans even when the content is unchanged.
