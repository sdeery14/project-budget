"""One place to ask what time it is, so tests can freeze it by passing ``now``."""

from datetime import UTC, date, datetime

from projectbudget import localtime


def utcnow() -> datetime:
    """Naive UTC: what the dialect-neutral ``DateTime`` columns store (ADR-001)."""
    return datetime.now(UTC).replace(tzinfo=None)


def today() -> date:
    """Today in the workspace time zone (#27): the date a default or a one-click purchase gets."""
    return localtime.today()
