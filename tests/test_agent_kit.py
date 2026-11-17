"""The agent kit under docs/agents states only rules the manifest parser enforces (#79, ADR-013).

The kit is copied into other repositories, so it drifts silently when the format changes. Each
rule the two texts state is checked here against the parser's behaviour or constants.
"""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from projectbudget.adapters.manifest import CSV_COLUMNS, ItemDocument, parse_yaml
from projectbudget.cli.main import app
from projectbudget.domain.catalog import NAME_MAX_LENGTH, SLUG_MAX_LENGTH, UNIT_MAX_LENGTH
from projectbudget.domain.manifest import MANIFEST_VERSION, ManifestError

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "docs" / "agents"
SECTION = KIT / "AGENTS-section.md"
SKILL = KIT / "skills" / "shopping-list" / "SKILL.md"
TEXTS = {
    SECTION.name: SECTION.read_text(encoding="utf-8"),
    SKILL.name: SKILL.read_text(encoding="utf-8"),
}
VALIDATE = "uvx projectbudget manifest validate project-budget.yaml"


def _manifest(items: str) -> str:
    return (
        f"version: {MANIFEST_VERSION}\nproject: {{id: garden-gate, name: Garden gate}}\n"
        f"items:\n{items}"
    )


def _rejects(items: str, *words: str) -> None:
    with pytest.raises(ManifestError) as caught:
        parse_yaml(_manifest(items))
    message = str(caught.value).lower()
    assert all(word in message for word in words), message


def test_kit_files_exist_and_the_readme_table_points_at_them() -> None:
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    for name in ("AGENTS-section.md", "skills/shopping-list/SKILL.md"):
        assert name in readme
        assert (KIT / name).is_file(), name
    assert "docs/agents" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert (ROOT / "docs" / "manifest.schema.json").is_file()


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_the_validate_command_the_kit_names_exists(name: str) -> None:
    assert VALIDATE in TEXTS[name], name
    result = CliRunner().invoke(app, ["manifest", "validate", "--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_stated_limits_match_the_parser_constants(name: str) -> None:
    text = TEXTS[name]
    assert f"1 to {SLUG_MAX_LENGTH} characters" in text, name
    assert f"at most {NAME_MAX_LENGTH} characters" in text, name
    assert f"at most {UNIT_MAX_LENGTH}" in text, name
    assert f"version: {MANIFEST_VERSION}" in text or f"`version` is `{MANIFEST_VERSION}`" in text


@pytest.mark.parametrize("name", sorted(TEXTS))
def test_stated_defaults_match_the_item_model(name: str) -> None:
    text = TEXTS[name]
    fields = ItemDocument.model_fields
    assert fields["unit"].default == "each"
    assert "default `each`" in text or "default: each" in text
    assert fields["priority"].default == 2
    assert "default 2" in text or "2 default" in text
    assert fields["kind"].default.value == "consumable"
    assert "`consumable`" in text
    assert fields["sharing"].default.value == "shared"
    assert "`shared`" in text


def test_the_section_example_parses_as_written() -> None:
    block = re.search(r"```yaml\n(.*?)```", TEXTS[SECTION.name], re.S)
    assert block is not None
    manifest = parse_yaml(block.group(1))
    assert manifest.project_id == "garden-gate"
    assert [item.id for item in manifest.items] == [
        "gate-hinge-set",
        "carriage-bolts",
        "post-level",
    ]
    assert manifest.items[0].estimate is not None
    assert manifest.items[0].estimate.amount == "24.00"


def test_rules_the_kit_states_are_rules_the_parser_enforces() -> None:
    ok = "  - {id: post-level, name: Post level, qty: 1}\n"
    assert len(parse_yaml(_manifest(ok)).items) == 1
    # A bare YAML number as an amount is rejected; a quoted decimal string is not.
    _rejects("  - {id: post-level, name: Post level, qty: 1, estimate: {amount: 9.50}}\n", "amount")
    parse_yaml(
        _manifest('  - {id: post-level, name: Post level, qty: 1, estimate: {amount: "9.50"}}\n')
    )
    _rejects(
        '  - {id: post-level, name: Post level, qty: 1, estimate: {amount: "9.505"}}\n', "amount"
    )
    # Ids are slugs and unique.
    _rejects("  - {id: Post_Level, name: Post level, qty: 1}\n", "lower-case")
    _rejects(ok + ok, "duplicate")
    # qty at least 1, priority 1 to 3, unknown fields are errors.
    _rejects("  - {id: post-level, name: Post level, qty: 0}\n", "qty")
    _rejects("  - {id: post-level, name: Post level, qty: 1, priority: 4}\n", "priority")
    _rejects("  - {id: post-level, name: Post level, qty: 1, priority: 0}\n", "priority")
    _rejects("  - {id: post-level, name: Post level, qty: 1, colour: red}\n", "colour")
    # Only version 1 exists.
    with pytest.raises(ManifestError):
        parse_yaml(_manifest(ok).replace(f"version: {MANIFEST_VERSION}", "version: 2"))


def test_csv_columns_in_the_guide_match_the_parser() -> None:
    guide = (ROOT / "docs" / "manifest.md").read_text(encoding="utf-8")
    assert ",".join(CSV_COLUMNS) in guide
