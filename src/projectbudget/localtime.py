"""The workspace time zone: the one place that turns an instant into the owner's date.

Dates the owner types are stored as written. Dates the app derives from an instant
(today, a file's modification time, a commit time) go through ``local_date`` first, so a
sync at 19:04 in New York no longer records estimates dated tomorrow (#27, ADR-012).
Timestamps shown on a page go through ``to_local`` and are labelled with ``label()``.

The zone is ``PROJECTBUDGET_TIMEZONE`` (an IANA name), applied with ``configure`` when
the app or the CLI starts. Unset, the server's own zone is used: ``TZ`` if set, else
``/etc/localtime``, else whatever the C library reports for right now.
"""

import os
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_configured: tzinfo | None = None
_detected: tzinfo | None = None


class UnknownTimezoneError(ValueError):
    """Not an IANA zone name."""


def resolve(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise UnknownTimezoneError(
            f"Unknown time zone {name!r}; use an IANA name such as Europe/Amsterdam"
        ) from exc


def configure(name: str | None) -> None:
    """Set the workspace zone for this process; ``None`` goes back to the server's zone."""
    global _configured, _detected
    _configured = resolve(name) if name else None
    _detected = None


def zone() -> tzinfo:
    global _detected
    if _configured is not None:
        return _configured
    if _detected is None:
        _detected = _server_zone()
    return _detected


def _server_zone() -> tzinfo:
    name = os.environ.get("TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    try:
        parts = Path("/etc/localtime").resolve(strict=True).parts
        if "zoneinfo" in parts:
            return ZoneInfo("/".join(parts[parts.index("zoneinfo") + 1 :]))
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    # A fixed offset for right now: correct for today, approximate across DST changes.
    return datetime.now().astimezone().tzinfo or UTC


def label() -> str:
    """What to print next to a converted time: the IANA key, or the C library's name."""
    current = zone()
    key = getattr(current, "key", None)
    if key:
        return str(key)
    return datetime.now(current).tzname() or "UTC"


def to_local(instant: datetime) -> datetime:
    """A naive UTC timestamp (what the database stores) as an aware local one."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(zone())


def local_date(instant: datetime) -> date:
    """The owner's calendar date for an instant; naive input is taken as UTC."""
    return to_local(instant).date()


def today() -> date:
    return datetime.now(zone()).date()
