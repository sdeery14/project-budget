"""API tokens: random, shown once, stored as SHA-256, revocable (ADR-005).

Only a browser session may issue or revoke tokens. A leaked token can then be
revoked from the UI but cannot mint replacements for itself.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import ApiToken, Owner
from projectbudget.persistence.session import SessionFactory
from projectbudget.services.auth import SESSION_TOUCH_INTERVAL, hash_secret
from projectbudget.services.clock import utcnow

API_KEY_PREFIX = "pb_"
TOKEN_SECRET_BYTES = 32
TOKEN_NAME_MAX_LENGTH = 100


class TokenManagementRequiresSessionError(PermissionError):
    """Tokens are managed from a browser session, never with another token."""


class InvalidTokenNameError(ValueError):
    """Empty or too long."""


@dataclass(frozen=True, slots=True)
class TokenInfo:
    id: str
    name: str
    read_only: bool
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class IssuedToken:
    info: TokenInfo
    secret: str


def _require_session(principal: Principal) -> None:
    if principal.auth != "session":
        raise TokenManagementRequiresSessionError


def _info(row: ApiToken) -> TokenInfo:
    return TokenInfo(
        id=row.id,
        name=row.name,
        read_only=row.read_only,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def issue_token(
    session_factory: SessionFactory,
    principal: Principal,
    name: str,
    *,
    read_only: bool,
    now: datetime | None = None,
) -> IssuedToken:
    _require_session(principal)
    clean_name = name.strip()
    if not clean_name or len(clean_name) > TOKEN_NAME_MAX_LENGTH:
        msg = f"Token name must be 1-{TOKEN_NAME_MAX_LENGTH} characters"
        raise InvalidTokenNameError(msg)
    secret = API_KEY_PREFIX + secrets.token_urlsafe(TOKEN_SECRET_BYTES)
    with session_factory.begin() as db:
        row = ApiToken(
            id=str(uuid.uuid4()),
            owner_id=principal.owner_id,
            name=clean_name,
            token_hash=hash_secret(secret),
            read_only=read_only,
            created_at=now or utcnow(),
            last_used_at=None,
            revoked_at=None,
        )
        db.add(row)
        db.flush()
        return IssuedToken(info=_info(row), secret=secret)


def list_tokens(session_factory: SessionFactory, principal: Principal) -> list[TokenInfo]:
    with session_factory() as db:
        rows = db.scalars(
            select(ApiToken)
            .where(ApiToken.owner_id == principal.owner_id)
            .order_by(ApiToken.created_at.desc(), ApiToken.id)
        ).all()
        return [_info(row) for row in rows]


def revoke_token(
    session_factory: SessionFactory,
    principal: Principal,
    token_id: str,
    *,
    now: datetime | None = None,
) -> TokenInfo | None:
    """Revoke a token; returns its state, or None when it is not this owner's."""
    _require_session(principal)
    with session_factory.begin() as db:
        row = db.get(ApiToken, token_id)
        if row is None or row.owner_id != principal.owner_id:
            return None
        if row.revoked_at is None:
            row.revoked_at = now or utcnow()
        return _info(row)


def resolve_token(
    session_factory: SessionFactory,
    secret: str,
    *,
    now: datetime | None = None,
) -> Principal | None:
    """Turn a bearer token into a principal, or None for unknown or revoked tokens."""
    moment = now or utcnow()
    token_hash = hash_secret(secret)
    with session_factory.begin() as db:
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if row is None or row.revoked_at is not None:
            return None
        owner = db.get(Owner, row.owner_id)
        if owner is None:
            return None
        if row.last_used_at is None or moment - row.last_used_at >= SESSION_TOUCH_INTERVAL:
            row.last_used_at = moment
        return Principal(
            owner_id=owner.id, username=owner.username, auth="token", read_only=row.read_only
        )
