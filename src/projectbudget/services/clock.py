"""One place to ask what time it is, so tests can freeze it by passing ``now``."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC: what the dialect-neutral ``DateTime`` columns store (ADR-001)."""
    return datetime.now(UTC).replace(tzinfo=None)
