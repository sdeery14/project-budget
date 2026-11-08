"""Regenerate ``docs/manifest.schema.json`` from the manifest model (RFC v3 §6).

Run with ``uv run python scripts/make_manifest_schema.py``. A test fails when the
committed file differs from what the model generates.
"""

import json
from pathlib import Path

from projectbudget.adapters.manifest import manifest_json_schema

TARGET = Path(__file__).resolve().parent.parent / "docs" / "manifest.schema.json"


def render() -> str:
    return json.dumps(manifest_json_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    TARGET.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {TARGET.relative_to(TARGET.parent.parent)}")  # noqa: T201


if __name__ == "__main__":
    main()
