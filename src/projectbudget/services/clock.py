"""One place to ask what time it is, so tests can freeze it by passing ``now``."""

from datetime import UTC, date, datetime


def utcnow() -> datetime:
    """Naive UTC: what the dialect-neutral ``DateTime`` columns store (ADR-001)."""
    return datetime.now(UTC).replace(tzinfo=None)


def today() -> date:
    """Today in UTC; dates such as an estimate's ``as_of`` carry no time zone."""
    return utcnow().date()
