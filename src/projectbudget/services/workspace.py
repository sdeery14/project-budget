"""The workspace's one setting in V1: its currency (ADR-003).

``projectbudget init`` sets it; every money write is checked against it. The
row is created lazily so databases migrated before this table existed keep
working with the default.
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from projectbudget.domain.money import validate_currency
from projectbudget.persistence.models import WorkspaceSettings
from projectbudget.persistence.session import SessionFactory
from projectbudget.services.clock import utcnow

WORKSPACE_ID = "workspace"
DEFAULT_CURRENCY = "USD"


def _read_currency(session_factory: SessionFactory) -> str | None:
    with session_factory() as db:
        row = db.get(WorkspaceSettings, WORKSPACE_ID)
        return row.currency if row is not None else None


def get_currency(session_factory: SessionFactory) -> str:
    """The workspace currency, creating the settings row with the default if it is missing."""
    currency = _read_currency(session_factory)
    if currency is not None:
        return currency
    try:
        with session_factory.begin() as db:
            db.add(
                WorkspaceSettings(id=WORKSPACE_ID, currency=DEFAULT_CURRENCY, updated_at=utcnow())
            )
    except IntegrityError:
        pass  # a concurrent caller created it; both wanted the same default
    currency = _read_currency(session_factory)
    if currency is None:
        raise RuntimeError("Workspace settings row disappeared right after it was created")
    return currency


def set_currency(
    session_factory: SessionFactory, currency: str, *, now: datetime | None = None
) -> str:
    """Set the workspace currency (``init`` only). Returns the normalised code."""
    code = validate_currency(currency)
    with session_factory.begin() as db:
        row = db.get(WorkspaceSettings, WORKSPACE_ID)
        if row is None:
            db.add(WorkspaceSettings(id=WORKSPACE_ID, currency=code, updated_at=now or utcnow()))
        else:
            row.currency = code
            row.updated_at = now or utcnow()
    return code
