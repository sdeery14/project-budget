"""Templates stay inside the CSP: no hx-on, no js:, no inline script or style (ADR-002)."""

import re
from pathlib import Path

import pytest

from projectbudget.web.templates import TEMPLATES_DIR

TEMPLATES = sorted(TEMPLATES_DIR.rglob("*.html"))
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
FORM_TAG = re.compile(r"<form\b[^>]*>|</form>", re.IGNORECASE)
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


def _owning_form(text: str, position: int) -> str | None:
    """The opening tag of the form still open at ``position``; closed forms do not count."""
    stack: list[str] = []
    for tag in FORM_TAG.finditer(text, 0, position):
        if tag.group(0).lower().startswith("</"):
            if stack:
                stack.pop()
        else:
            stack.append(tag.group(0))
    return stack[-1] if stack else None


def unconfirmed_danger_buttons(text: str) -> list[str]:
    """Red buttons whose own form has neither data-confirm nor hx-confirm (#17)."""
    found = []
    for match in DANGER_BUTTON.finditer(text):
        form = _owning_form(text, match.start())
        if form is None or not ("data-confirm=" in form or "hx-confirm=" in form):
            found.append(match.group(0))
    return found


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES_DIR)))
def test_every_destructive_button_asks_first(template: Path) -> None:
    assert unconfirmed_danger_buttons(template.read_text(encoding="utf-8")) == []


def test_destructive_button_check_looks_at_the_owning_form_only() -> None:
    confirmed = '<form data-confirm="Sure?"><button class="danger">A</button></form>'
    bare = '<form method="post"><button type="submit" class="danger">B</button></form>'
    htmx = '<form hx-post="/x" hx-confirm="Sure?"><button class="danger">C</button></form>'
    # A confirmation on an earlier, already closed form must not cover the next form's button.
    assert unconfirmed_danger_buttons(confirmed + bare) == ['<button type="submit" class="danger"']
    assert unconfirmed_danger_buttons(htmx + confirmed) == []
    assert unconfirmed_danger_buttons('<button class="danger">loose</button>') == [
        '<button class="danger"'
    ]
