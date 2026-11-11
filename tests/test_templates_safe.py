"""Templates stay inside the CSP: no hx-on, no js:, no inline script or style (ADR-002)."""

import re
from pathlib import Path

import pytest

from projectbudget.web.templates import TEMPLATES_DIR

TEMPLATES = sorted(TEMPLATES_DIR.rglob("*.html"))
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
FORM = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
DANGER_BUTTON = re.compile(r'<button\b[^>]*class="[^"]*\bdanger\b[^"]*"', re.IGNORECASE)


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


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES_DIR)))
def test_every_destructive_button_asks_first(template: Path) -> None:
    """A red button sits in a form with data-confirm, or on an htmx element with hx-confirm (#17)."""
    text = template.read_text(encoding="utf-8")
    for match in DANGER_BUTTON.finditer(text):
        before = text[: match.start()]
        opener = before[before.rfind("<form") :] if "<form" in before else ""
        form_tag = FORM.match(opener)
        asks = (form_tag is not None and "data-confirm=" in form_tag.group(0)) or (
            "hx-confirm=" in opener
        )
        assert asks, f"{template.name}: {match.group(0)} submits without a confirmation"
