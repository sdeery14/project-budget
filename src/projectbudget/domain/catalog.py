"""Vocabulary and validation for projects, catalog items, requirements and estimates.

Pure Python (RFC v3 §3): the enumerations are stored as their string values so
both databases see plain ``VARCHAR`` columns (ADR-001). Everything here is also
exactly what the manifest and the API will accept later.
"""

import unicodedata
from enum import StrEnum

SLUG_MAX_LENGTH = 100
NAME_MAX_LENGTH = 200
UNIT_MAX_LENGTH = 30
ARCHIVE_REASON_MAX_LENGTH = 255
DEFAULT_UNIT = "each"
PRIORITY_MIN = 1
PRIORITY_MAX = 3
_SLUG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


class ItemKind(StrEnum):
    """Consumables are used up; reusables (tools) are owned once and shared (ADR-004)."""

    CONSUMABLE = "consumable"
    REUSABLE = "reusable"


class ReviewState(StrEnum):
    """Items created from free text or a manifest start unreviewed."""

    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"


class Sharing(StrEnum):
    """How a reusable requirement counts in consolidation (ADR-004). Ignored for consumables."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class Intent(StrEnum):
    """What the owner wants for a requirement. Only ``active`` ones drive the backlog."""

    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EstimateOrigin(StrEnum):
    MANUAL = "manual"
    MANIFEST = "manifest"
    PURCHASE = "purchase"


class InvalidSlugError(ValueError):
    """Not lower-case ASCII letters, digits and single hyphens, 1-100 characters."""


class InvalidNameError(ValueError):
    """Empty or too long."""


class InvalidUnitError(ValueError):
    """Empty, too long, or contains whitespace."""


class InvalidQuantityError(ValueError):
    """Quantities are whole numbers greater than zero."""


class InvalidPriorityError(ValueError):
    """Priority is a tier from 1 (most urgent) to 3."""


def slugify(text: str) -> str:
    """Derive a slug from free text: ``"M5 x 20 Bolts (steel)"`` becomes ``m5-x-20-bolts-steel``."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    parts: list[str] = []
    current: list[str] = []
    for ch in ascii_text.lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            parts.append("".join(current))
            current = []
    if current:
        parts.append("".join(current))
    slug = "-".join(parts)[:SLUG_MAX_LENGTH].rstrip("-")
    return validate_slug(slug)


def validate_slug(slug: str) -> str:
    """Slugs are the stable identity manifests use, so they are strict and immutable."""
    if not slug or len(slug) > SLUG_MAX_LENGTH:
        raise InvalidSlugError(f"Slug must be 1-{SLUG_MAX_LENGTH} characters, got {slug!r}")
    for part in slug.split("-"):
        if not part or any(ch not in _SLUG_CHARS for ch in part):
            msg = f"Slug must be lower-case ASCII letters, digits and single hyphens, got {slug!r}"
            raise InvalidSlugError(msg)
    return slug


def validate_name(name: str) -> str:
    clean = " ".join(name.split())
    if not clean or len(clean) > NAME_MAX_LENGTH:
        raise InvalidNameError(f"Name must be 1-{NAME_MAX_LENGTH} characters")
    return clean


def validate_unit(unit: str) -> str:
    clean = unit.strip().lower()
    if not clean or len(clean) > UNIT_MAX_LENGTH or any(ch.isspace() for ch in clean):
        raise InvalidUnitError(f"Unit must be 1-{UNIT_MAX_LENGTH} characters with no spaces")
    return clean


def validate_quantity(qty: int) -> int:
    if type(qty) is not int or qty <= 0:
        raise InvalidQuantityError(f"Quantity must be a whole number above zero, got {qty!r}")
    return qty


def validate_priority(priority: int) -> int:
    if type(priority) is not int or not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        msg = f"Priority must be {PRIORITY_MIN}-{PRIORITY_MAX}, got {priority!r}"
        raise InvalidPriorityError(msg)
    return priority
