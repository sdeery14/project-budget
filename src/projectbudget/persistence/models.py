"""ORM models. Dialect-neutral subset only (ADR-001).

UUIDs are stored as text, timestamps as naive UTC ``DateTime`` so SQLite and
PostgreSQL behave identically. Secrets never land here in clear text: session
ids and API tokens are stored as SHA-256 hashes, passwords as Argon2id hashes.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from projectbudget.persistence.base import Base

UUID_LENGTH = 36
SHA256_HEX_LENGTH = 64


class Owner(Base):
    """The single owner (ADR-005). ``projectbudget init`` creates the one row."""

    __tablename__ = "owners"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime())


class LoginSession(Base):
    """A browser session. The cookie holds a random secret; ``id`` is its SHA-256."""

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("owners.id", ondelete="CASCADE"), index=True
    )
    csrf_token: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime())
    expires_at: Mapped[datetime] = mapped_column(DateTime())


class LoginAttempt(Base):
    """Login rate-limit bucket, keyed by username or client address (ADR-005)."""

    __tablename__ = "login_attempts"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer())
    window_started_at: Mapped[datetime] = mapped_column(DateTime())
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class ApiToken(Base):
    """A bearer token for ``/api/v1``; shown once, stored hashed, revocable."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("owners.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    read_only: Mapped[bool] = mapped_column(Boolean())
    created_at: Mapped[datetime] = mapped_column(DateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
