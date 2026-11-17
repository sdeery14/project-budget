"""A plain ``pytest`` run leaves ``tests/e2e`` out; naming it collects it (``tests/conftest.py``)."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _collected(*targets: str) -> list[str]:
    """Node ids a ``pytest --collect-only`` run in the repository root would execute."""
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    result = subprocess.run(  # noqa: S603  (our own interpreter, a fixed argument list)
        [*command, *targets], cwd=ROOT, capture_output=True, text=True, check=False, timeout=180
    )
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if "::" in line]


def test_a_plain_run_leaves_the_browser_tests_out() -> None:
    items = _collected()
    assert items
    assert not [item for item in items if item.startswith("tests/e2e/")]


@pytest.mark.parametrize(
    "target",
    [
        "tests/e2e",
        "tests/e2e/test_smoke.py",
        "tests/e2e/test_smoke.py::test_core_journey_in_the_browser",
    ],
)
def test_naming_the_browser_tests_collects_them(target: str) -> None:
    pytest.importorskip("pytest_playwright")
    items = _collected(target)
    assert items, target
    assert all(item.startswith("tests/e2e/") for item in items), items
