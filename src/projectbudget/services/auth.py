"""Owner, login sessions and the login rate limit (ADR-005).

Sessions and rate-limit buckets are rows, never process memory, so any number
of workers behave the same and a restart logs nobody out (ADR-002).
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import DateTime, case, delete, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import LoginAttempt, LoginSession, Owner
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import passwords
from projectbudget.services.clock import utcnow

SESSION_SECRET_BYTES = 32
SESSION_MAX_AGE = timedelta(days=30)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCKOUT = timedelta(minutes=15)

USERNAME_MAX_LENGTH = 100


class OwnerExistsError(Exception):
    """``init`` was run on a database that already has its owner."""


class InvalidUsernameError(ValueError):
    """Empty, too long, or contains whitespace."""


class LoginLockedError(Exception):
    """Too many failures; try again after ``retry_after``."""

    def __init__(self, retry_after: timedelta) -> None:
        super().__init__(f"Too many failed logins; try again in {retry_after}")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class OwnerInfo:
    id: str
    username: str


@dataclass(frozen=True, slots=True)
class NewSession:
    cookie_value: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    id: str
    principal: Principal
    csrf_token: str


def hash_secret(value: str) -> str:
    """SHA-256 hex of a high-entropy secret (session id or API token). Not for passwords."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def secrets_match(expected: str, provided: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not name or len(name) > USERNAME_MAX_LENGTH or any(ch.isspace() for ch in name):
        msg = f"Username must be 1-{USERNAME_MAX_LENGTH} characters with no spaces"
        raise InvalidUsernameError(msg)
    return name


# --- owner -------------------------------------------------------------------------


def owner_exists(session_factory: SessionFactory) -> bool:
    with session_factory() as db:
        return db.scalar(select(Owner.id).limit(1)) is not None


def create_owner(
    session_factory: SessionFactory,
    username: str,
    password: str,
    *,
    now: datetime | None = None,
) -> OwnerInfo:
    """Create the single owner. Fails if one exists or the password is too short."""
    name = normalize_username(username)
    password_hash = passwords.hash_password(password)
    with session_factory.begin() as db:
        if db.scalar(select(Owner.id).limit(1)) is not None:
            raise OwnerExistsError
        owner = Owner(
            id=str(uuid.uuid4()),
            username=name,
            password_hash=password_hash,
            created_at=now or utcnow(),
        )
        db.add(owner)
        return OwnerInfo(id=owner.id, username=owner.username)


def authenticate(
    session_factory: SessionFactory,
    username: str,
    password: str,
    *,
    limit_keys: Sequence[str],
    now: datetime | None = None,
) -> OwnerInfo | None:
    """Check credentials under the rate limit.

    ``limit_keys`` are the buckets this attempt counts against, typically the
    username and the client address. A locked bucket raises before any check;
    a failure counts against every bucket; a success clears them.
    """
    moment = now or utcnow()
    keys = sorted(set(limit_keys))
    try:
        name: str | None = normalize_username(username)
    except InvalidUsernameError:
        name = None
    _ensure_buckets(session_factory, keys, moment)
    with session_factory.begin() as db:
        _raise_if_locked(db, keys, moment)
        owner = db.scalar(select(Owner).where(Owner.username == name)) if name is not None else None
        verified = passwords.verify_or_burn(owner.password_hash if owner else None, password)
        if owner is not None and verified:
            db.execute(delete(LoginAttempt).where(LoginAttempt.key.in_(keys)))
            if passwords.needs_rehash(owner.password_hash):
                owner.password_hash = passwords.hash_password(password)
            return OwnerInfo(id=owner.id, username=owner.username)
        _record_failure(db, keys, moment)
        return None


def _raise_if_locked(db: Session, keys: Sequence[str], now: datetime) -> None:
    rows = db.scalars(select(LoginAttempt).where(LoginAttempt.key.in_(list(keys)))).all()
    locked = [row.locked_until for row in rows if row.locked_until and row.locked_until > now]
    if locked:
        raise LoginLockedError(max(locked) - now)


def _ensure_buckets(session_factory: SessionFactory, keys: Sequence[str], now: datetime) -> None:
    """Make sure a bucket row exists for every key, each in its own short transaction.

    Two concurrent attempts may both try to create the same row; the loser's
    IntegrityError rolls back only this small transaction and the row it wanted
    is there. Afterwards the main transaction only ever runs atomic UPDATEs.
    """
    for key in keys:
        try:
            with session_factory.begin() as db:
                if db.get(LoginAttempt, key) is None:
                    db.add(
                        LoginAttempt(key=key, failures=0, window_started_at=now, locked_until=None)
                    )
        except IntegrityError:
            pass


def _record_failure(db: Session, keys: Sequence[str], now: datetime) -> None:
    """Count one failure per bucket in a single UPDATE each, so parallel attempts never lose one.

    Every expression reads the row as it was before this statement, on both
    backends, so the window reset, the increment and the lock decision are one
    atomic step. Keys are updated in sorted order so two attempts never lock the
    same rows in opposite orders.
    """
    window_expired = LoginAttempt.window_started_at <= literal(now - LOGIN_WINDOW, DateTime())
    new_failures = case((window_expired, 1), else_=LoginAttempt.failures + 1)
    for key in sorted(keys):
        db.execute(
            update(LoginAttempt)
            .where(LoginAttempt.key == key)
            .values(
                failures=new_failures,
                window_started_at=case(
                    (window_expired, literal(now, DateTime())),
                    else_=LoginAttempt.window_started_at,
                ),
                locked_until=case(
                    (new_failures >= LOGIN_MAX_FAILURES, literal(now + LOGIN_LOCKOUT, DateTime())),
                    else_=LoginAttempt.locked_until,
                ),
            )
        )


# --- sessions ----------------------------------------------------------------------


def create_session(
    session_factory: SessionFactory,
    owner_id: str,
    *,
    now: datetime | None = None,
) -> NewSession:
    """Start a session; the returned cookie value is the only copy of the secret."""
    moment = now or utcnow()
    cookie_value = secrets.token_urlsafe(SESSION_SECRET_BYTES)
    csrf_token = secrets.token_hex(32)
    with session_factory.begin() as db:
        db.execute(delete(LoginSession).where(LoginSession.expires_at <= moment))
        db.add(
            LoginSession(
                id=hash_secret(cookie_value),
                owner_id=owner_id,
                csrf_token=csrf_token,
                created_at=moment,
                last_seen_at=moment,
                expires_at=moment + SESSION_MAX_AGE,
            )
        )
    return NewSession(
        cookie_value=cookie_value, csrf_token=csrf_token, expires_at=moment + SESSION_MAX_AGE
    )


def resolve_session(
    session_factory: SessionFactory,
    cookie_value: str,
    *,
    idle_timeout: timedelta,
    now: datetime | None = None,
) -> ResolvedSession | None:
    """Look a cookie up; expired or idle sessions are deleted and return None."""
    moment = now or utcnow()
    session_id = hash_secret(cookie_value)
    with session_factory.begin() as db:
        row = db.get(LoginSession, session_id)
        if row is None:
            return None
        if row.expires_at <= moment or row.last_seen_at + idle_timeout <= moment:
            db.delete(row)
            return None
        if moment - row.last_seen_at >= SESSION_TOUCH_INTERVAL:
            row.last_seen_at = moment
        owner = db.get(Owner, row.owner_id)
        if owner is None:
            db.delete(row)
            return None
        principal = Principal(owner_id=owner.id, username=owner.username, auth="session")
        return ResolvedSession(id=row.id, principal=principal, csrf_token=row.csrf_token)


def revoke_session(session_factory: SessionFactory, session_id: str) -> None:
    with session_factory.begin() as db:
        db.execute(delete(LoginSession).where(LoginSession.id == session_id))


def revoke_session_by_cookie(session_factory: SessionFactory, cookie_value: str) -> None:
    revoke_session(session_factory, hash_secret(cookie_value))
