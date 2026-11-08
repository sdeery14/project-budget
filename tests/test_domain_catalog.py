"""Slugs, names, units, quantities and priorities: the pure vocabulary behind the catalog."""

import pytest

from projectbudget.domain.catalog import (
    SLUG_MAX_LENGTH,
    Intent,
    InvalidNameError,
    InvalidPriorityError,
    InvalidQuantityError,
    InvalidSlugError,
    InvalidUnitError,
    ItemKind,
    Sharing,
    slugify,
    validate_name,
    validate_priority,
    validate_quantity,
    validate_slug,
    validate_unit,
)


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("M5 x 20 Bolts (steel)", "m5-x-20-bolts-steel"),
        ("  Café  crème! ", "cafe-creme"),
        ("1/4-20 UNC", "1-4-20-unc"),
        ("clamps", "clamps"),
        ("Clamps", "clamps"),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug
    assert validate_slug(slug) == slug


def test_slugify_truncates_without_a_dangling_hyphen() -> None:
    long_text = " ".join(["ab"] * 60)  # 2 letters + hyphen per word, longer than the limit
    slug = slugify(long_text)
    assert len(slug) <= SLUG_MAX_LENGTH
    assert not slug.endswith("-")
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("text", ["", "   ", "!!!", "日本語"])
def test_slugify_needs_at_least_one_ascii_letter_or_digit(text: str) -> None:
    with pytest.raises(InvalidSlugError):
        slugify(text)


@pytest.mark.parametrize(
    "slug", ["", "Clamps", "has space", "a--b", "-a", "a-", "é", "a_b", "x" * 101]
)
def test_validate_slug_rejects_anything_but_lower_case_ascii_and_single_hyphens(
    slug: str,
) -> None:
    with pytest.raises(InvalidSlugError):
        validate_slug(slug)


def test_validate_name_collapses_whitespace() -> None:
    assert validate_name("  M5   bolts ") == "M5 bolts"
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(InvalidNameError):
            validate_name(bad)


def test_validate_unit() -> None:
    assert validate_unit(" Each ") == "each"
    assert validate_unit("m") == "m"
    for bad in ("", "square metres", "x" * 31):
        with pytest.raises(InvalidUnitError):
            validate_unit(bad)


def test_quantities_are_positive_whole_numbers() -> None:
    assert validate_quantity(1) == 1
    for bad in (0, -1, 2.5, True):
        with pytest.raises(InvalidQuantityError):
            validate_quantity(bad)  # type: ignore[arg-type]


def test_priority_is_a_tier_from_one_to_three() -> None:
    assert [validate_priority(p) for p in (1, 2, 3)] == [1, 2, 3]
    for bad in (0, 4, -1, 1.0, True):
        with pytest.raises(InvalidPriorityError):
            validate_priority(bad)  # type: ignore[arg-type]


def test_enumerations_are_their_string_values() -> None:
    """Stored as plain VARCHAR on both backends (ADR-001), so the values are the contract."""
    assert ItemKind("reusable") is ItemKind.REUSABLE
    assert str(ItemKind.CONSUMABLE) == "consumable"
    assert set(Sharing) == {"shared", "exclusive"}
    assert set(Intent) == {"active", "paused", "cancelled", "archived"}
    with pytest.raises(ValueError, match="tool"):
        ItemKind("tool")
