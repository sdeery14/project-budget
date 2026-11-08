"""Session factory: the handle services use to open transactions (RFC v3 §3)."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

SessionFactory = sessionmaker[Session]


def make_session_factory(engine: Engine) -> SessionFactory:
    """Sessions that keep loaded attributes readable after commit."""
    return sessionmaker(engine, expire_on_commit=False)
