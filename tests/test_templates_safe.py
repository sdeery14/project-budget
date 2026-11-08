"""Templates stay inside the CSP: no hx-on, no js:, no inline script or style (ADR-002)."""

import re
from pathlib import Path

import pytest

from projectbudget.web.templates import TEMPLATES_DIR

TEMPLATES = sorted(TEMPLATES_DIR.rglob("*.html"))
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def test_templates_exist() -> None:
    assert TEMPLATES


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES_DIR)))
def test_template_has_no_eval_or_inline_code(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert "hx-on" not in text
    assert "js:" not in text
    assert 'style="' not in text
    assert not INLINE_SCRIPT.search(text), "inline <script> blocks are blocked by the CSP"
    assert not INLINE_HANDLER.search(text), "on*= handlers are blocked by the CSP"
