"""Manifest v1 parsing: YAML and CSV, line-level schema errors, the committed JSON Schema."""

import json
from pathlib import Path

import pytest

from projectbudget.adapters import manifest as adapter
from projectbudget.domain.catalog import ItemKind, Sharing
from projectbudget.domain.manifest import ManifestError

SCHEMA_FILE = Path(__file__).parent.parent / "docs" / "manifest.schema.json"

GOOD = """
version: 1
project:
  id: hcalory-heater
  name: Diesel heater install
items:
  - id: fuel-line-6mm
    name: 6 mm fuel line
    qty: 3
    unit: m
    estimate: { amount: "12.50", currency: USD }
  - id: clamps
    name: Hose clamps
    qty: 6
    kind: reusable
    priority: 1
    sharing: exclusive
    catalog: hose-clamp
    notes: stainless
  - id: minor
    name: Minor-unit priced
    qty: 1
    estimate: { amount_minor: 999 }
"""


def test_parse_yaml_fills_defaults_and_normalises() -> None:
    manifest = adapter.parse_yaml(GOOD)
    assert manifest.version == 1
    assert (manifest.project_id, manifest.project_name) == (
        "hcalory-heater",
        "Diesel heater install",
    )
    fuel, clamps, minor = manifest.items
    assert (fuel.qty, fuel.unit, fuel.kind, fuel.priority, fuel.sharing) == (
        3,
        "m",
        ItemKind.CONSUMABLE,
        2,
        Sharing.SHARED,
    )
    assert fuel.estimate is not None
    assert fuel.estimate.to_money("USD").amount == 1250
    assert (clamps.kind, clamps.priority, clamps.sharing, clamps.catalog, clamps.notes) == (
        ItemKind.REUSABLE,
        1,
        Sharing.EXCLUSIVE,
        "hose-clamp",
        "stainless",
    )
    assert clamps.unit == "each"
    assert minor.estimate is not None
    assert minor.estimate.to_money("USD").amount == 999
    assert manifest.by_id()["clamps"] is clamps


def test_project_name_defaults_to_the_id() -> None:
    manifest = adapter.parse_yaml("version: 1\nproject: {id: shed}\nitems: []\n")
    assert manifest.project_name == "shed"
    assert manifest.items == ()


def _issues(text: str) -> list[str]:
    with pytest.raises(ManifestError) as excinfo:
        adapter.parse_yaml(text)
    return [str(issue) for issue in excinfo.value.issues]


def test_float_amount_is_a_schema_error_with_its_line() -> None:
    text = "version: 1\nproject: {id: shed}\nitems:\n  - id: a\n    name: A\n    qty: 1\n    estimate: {amount: 12.50}\n"
    issues = _issues(text)
    assert len(issues) == 1
    assert issues[0].startswith("line 7, items[0].estimate.amount")
    assert "quoted decimal string" in issues[0]


def test_missing_required_fields_and_bad_values_report_lines() -> None:
    text = "version: 1\nproject: {id: shed}\nitems:\n  - name: no id\n    qty: 0\n  - id: Bad Id\n    name: x\n    qty: 2\n    priority: 7\n"
    issues = _issues(text)
    joined = "\n".join(issues)
    assert "line 4, items[0].id" in joined  # missing: reported at the item
    assert "line 5, items[0].qty" in joined
    assert "line 6, items[1].id" in joined  # not a slug
    assert "line 9, items[1].priority" in joined


def test_duplicate_ids_reject_the_whole_file() -> None:
    text = "version: 1\nproject: {id: shed}\nitems:\n  - {id: a, name: A, qty: 1}\n  - {id: a, name: B, qty: 2}\n"
    issues = _issues(text)
    assert any("duplicate item id 'a'" in issue and "line 5" in issue for issue in issues)


def test_unknown_fields_wrong_version_and_invalid_yaml() -> None:
    assert any(
        "extra" in i.lower()
        for i in _issues("version: 1\nproject: {id: shed}\nitems: []\nbonus: 1\n")
    )
    assert any("version" in i for i in _issues("version: 2\nproject: {id: shed}\nitems: []\n"))
    bad_yaml = _issues("version: 1\nproject: {id: shed\nitems: []\n")
    assert bad_yaml[0].startswith("line")
    assert "invalid YAML" in bad_yaml[0]
    assert "mapping" in _issues("- just\n- a list\n")[0]


def test_bytes_input_and_size_limit() -> None:
    assert adapter.parse_yaml(GOOD.encode("utf-8")).project_id == "hcalory-heater"
    with pytest.raises(ManifestError, match="not UTF-8"):
        adapter.parse_yaml(b"\xff\xfe\xfa")
    with pytest.raises(ManifestError, match="2 MB"):
        adapter.parse_yaml(b"x" * (adapter.MAX_MANIFEST_BYTES + 1))


def test_estimate_needs_an_amount() -> None:
    issues = _issues(
        "version: 1\nproject: {id: shed}\nitems:\n  - {id: a, name: A, qty: 1, estimate: {currency: USD}}\n"
    )
    assert "amount or amount_minor" in issues[0]


CSV = """id,name,qty,unit,kind,priority,sharing,catalog,estimate
hinge,Gate hinge set,1,set,,1,,,
post,Fence post,4,,consumable,,,fence-post,18.00
"""


def test_parse_csv_uses_the_source_project_id_and_defaults() -> None:
    manifest = adapter.parse_csv(CSV, project_id="garden-gate")
    assert manifest.project_id == manifest.project_name == "garden-gate"
    hinge, post = manifest.items
    assert (hinge.qty, hinge.unit, hinge.priority, hinge.kind) == (1, "set", 1, ItemKind.CONSUMABLE)
    assert hinge.estimate is None
    assert post.catalog == "fence-post"
    assert post.estimate is not None
    assert post.estimate.amount == "18.00"
    assert (
        adapter.parse_csv(CSV, project_id="garden-gate", project_name="Garden gate").project_name
        == "Garden gate"
    )


def test_csv_errors_carry_row_numbers_and_the_header_is_fixed() -> None:
    with pytest.raises(ManifestError) as excinfo:
        adapter.parse_csv("id,name\nx,y\n", project_id="p")
    assert "columns must be exactly" in str(excinfo.value.issues[0])
    with pytest.raises(ManifestError) as excinfo:
        adapter.parse_csv(CSV + "bolt,Bolt,lots,,,,,,\n", project_id="p")
    assert any("line 4, items[2].qty" in str(i) for i in excinfo.value.issues)
    with pytest.raises(ManifestError) as excinfo:
        adapter.parse_csv(CSV + "hinge,Again,1,,,,,,\n", project_id="p")
    assert any("duplicate item id 'hinge'" in str(i) for i in excinfo.value.issues)


def test_committed_json_schema_matches_the_model() -> None:
    generated = json.dumps(adapter.manifest_json_schema(), indent=2, sort_keys=True) + "\n"
    assert SCHEMA_FILE.read_text(encoding="utf-8") == generated, (
        "docs/manifest.schema.json is stale; run `uv run python scripts/make_manifest_schema.py`"
    )
    schema = json.loads(generated)
    assert schema["title"] == "ProjectBudget manifest v1"
    estimate = schema["$defs"]["EstimateDocument"]["properties"]["amount"]
    assert any(option.get("pattern") == r"^\d+(\.\d{1,2})?$" for option in estimate["anyOf"])
