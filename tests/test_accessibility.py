"""Accessibility pass (Milestone 5): labelled controls, a skip link, a language, no empty buttons."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from projectbudget.web.templates import TEMPLATES_DIR

TEMPLATES = sorted(TEMPLATES_DIR.rglob("*.html"))
INPUT = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.IGNORECASE)
ATTR = re.compile(r'(\w[\w-]*)="([^"]*)"')
LABEL_FOR = re.compile(r'<label[^>]*\bfor="([^"]+)"', re.IGNORECASE)
BUTTON = re.compile(r"<button\b[^>]*>(.*?)</button>", re.IGNORECASE | re.DOTALL)


def _attrs(raw: str) -> dict[str, str]:
    return {key.lower(): value for key, value in ATTR.findall(raw)}


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES_DIR)))
def test_every_visible_control_has_a_label(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    labelled_ids = set(LABEL_FOR.findall(text))
    for tag, raw in INPUT.findall(text):
        attrs = _attrs(raw)
        kind = attrs.get("type", "text").lower()
        if kind in ("hidden", "submit"):
            continue
        if "aria-label" in attrs:
            continue
        if kind == "checkbox" and '<label class="check">' in text:
            continue  # wrapped in its label
        control_id = attrs.get("id")
        where = f"{template.name}: <{tag} name={attrs.get('name')!r}>"
        assert control_id, f"{where} needs an id with a <label for>, or an aria-label"
        assert control_id in labelled_ids, f"{where} has no <label for={control_id!r}>"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES_DIR)))
def test_buttons_have_text_or_an_accessible_name(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    for inner in BUTTON.findall(text):
        stripped = re.sub(r"<[^>]+>", "", inner).strip()
        assert stripped or "aria-label" in inner, f"{template.name}: a button without a name"


def test_base_layout_has_language_skip_link_and_main_landmark(logged_in: TestClient) -> None:
    page = logged_in.get("/backlog").text
    assert '<html lang="en">' in page
    assert 'class="skip-link" href="#main"' in page
    assert '<main id="main"' in page
    assert "<h1>" in page
    assert '<nav aria-label="Main">' in page
