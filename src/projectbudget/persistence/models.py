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
    merged_into_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    """Set when this item was merged into another: a redirect that keeps old slugs resolvable."""
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


class InventoryMovement(Base):
    """A fact about stock: received, consumed against a requirement, or an adjustment.

    On-hand is the sum of ``delta_qty``; consumption is the allocation to a project.
    ``purchase_line_id`` links a receipt to the purchase line it fulfils.
    """

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint("reason IN ('received', 'consumed', 'adjustment')", name="reason"),
        CheckConstraint("delta_qty <> 0", name="delta_nonzero"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    catalog_item_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id"), index=True
    )
    delta_qty: Mapped[int] = mapped_column(Integer())
    reason: Mapped[str] = mapped_column(String(20))
    requirement_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("requirements.id"), nullable=True, index=True
    )
    purchase_line_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("purchase_lines.id"), nullable=True, index=True
    )
    occurred_on: Mapped[date] = mapped_column(Date())
    note: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime())


class BacklogOrder(Base):
    """The workspace-level backlog order (ADR-004): one position per catalog item.

    The RFC lists this as ``backlog_order[]`` on the budget plan; arrays are outside
    the dialect-neutral subset (ADR-001), so it is a child table keyed by item.
    """

    __tablename__ = "backlog_order"

    catalog_item_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer())


class BudgetPlan(Base):
    """The one budget plan (ADR-006). ``earmarked_funds`` is the balance on ``balance_as_of``."""

    __tablename__ = "budget_plans"
    __table_args__ = (
        CheckConstraint("horizon_months BETWEEN 1 AND 120", name="horizon_range"),
        CheckConstraint("contingency_pct BETWEEN 0 AND 100", name="contingency_range"),
        CheckConstraint("protected_reserve >= 0", name="reserve_not_negative"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    earmarked_funds: Mapped[int] = mapped_column(BigInteger())
    balance_as_of: Mapped[date] = mapped_column(Date())
    protected_reserve: Mapped[int] = mapped_column(BigInteger())
    monthly_contribution: Mapped[int] = mapped_column(BigInteger())
    horizon_months: Mapped[int] = mapped_column(Integer())
    contingency_pct: Mapped[int] = mapped_column(Integer())
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class MonthOverride(Base):
    """A signed, labeled one-off for one month: a bonus, a tax bill, a holiday."""

    __tablename__ = "month_overrides"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("budget_plans.id", ondelete="CASCADE"), index=True
    )
    month: Mapped[date] = mapped_column(Date())
    delta: Mapped[int] = mapped_column(BigInteger())
    label: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime())


class Purchase(Base):
    """A fact: money spent (or committed) with a vendor. Never touched by sync (RFC v3 §4)."""

    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("status IN ('planned', 'ordered', 'received', 'cancelled')", name="status"),
        CheckConstraint("shipping >= 0", name="shipping_not_negative"),
        CheckConstraint("tax >= 0", name="tax_not_negative"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(200))
    ordered_on: Mapped[date] = mapped_column(Date())
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    shipping: Mapped[int] = mapped_column(BigInteger())
    tax: Mapped[int] = mapped_column(BigInteger())
    notes: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())


class PurchaseLine(Base):
    """One item on a purchase. Receiving a line creates an inventory movement."""

    __tablename__ = "purchase_lines"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_not_negative"),
        CheckConstraint("received_qty BETWEEN 0 AND qty", name="received_within_qty"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True)
    purchase_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("purchases.id", ondelete="CASCADE"), index=True
    )
    catalog_item_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("catalog_items.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer())
    qty: Mapped[int] = mapped_column(Integer())
    unit_price: Mapped[int] = mapped_column(BigInteger())
    received_qty: Mapped[int] = mapped_column(Integer())
