"""ORM models. Dialect-neutral subset only (ADR-001).

UUIDs are stored as text, timestamps as naive UTC ``DateTime`` so SQLite and
PostgreSQL behave identically. Secrets never land here in clear text: session
ids and API tokens are stored as SHA-256 hashes, passwords as Argon2id hashes.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
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


class WorkspaceSettings(Base):
    """The one workspace's settings; a single row (ADR-003: currency is set at ``init``)."""

    __tablename__ = "workspace_settings"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class Project(Base):
    """A project the owner is working on. Archived projects keep their requirements."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    archived: Mapped[bool] = mapped_column(Boolean())
    notes: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class CatalogItem(Base):
    """A thing that can be needed, priced and bought. The slug is its stable identity."""

    __tablename__ = "catalog_items"
    __table_args__ = (
        CheckConstraint("kind IN ('consumable', 'reusable')", name="kind"),
        CheckConstraint("review_state IN ('unreviewed', 'confirmed')", name="review_state"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(30))
    review_state: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class Requirement(Base):
    """A want: a project needs ``qty`` of a catalog item (RFC v3 §4)."""

    __tablename__ = "requirements"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        CheckConstraint("sharing IN ('shared', 'exclusive')", name="sharing"),
        CheckConstraint("intent IN ('active', 'paused', 'cancelled', 'archived')", name="intent"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id"), index=True
    )
    catalog_item_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id"), index=True
    )
    qty: Mapped[int] = mapped_column(Integer())
    priority: Mapped[int] = mapped_column(Integer())
    sort_order: Mapped[int] = mapped_column(Integer())
    intent: Mapped[str] = mapped_column(String(20))
    archive_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sharing: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class PriceEstimate(Base):
    """What a unit of an item is expected to cost, in minor units (ADR-003). Latest ``as_of`` wins."""

    __tablename__ = "price_estimates"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_not_negative"),
        CheckConstraint("confidence IN ('low', 'medium', 'high')", name="confidence"),
        CheckConstraint("origin IN ('manual', 'manifest', 'purchase')", name="origin"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    catalog_item_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[int] = mapped_column(BigInteger())
    currency: Mapped[str] = mapped_column(String(3))
    as_of: Mapped[date] = mapped_column(Date())
    confidence: Mapped[str] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime())
