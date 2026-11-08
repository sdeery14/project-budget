"""Owner, sessions and the database-backed login rate limit (ADR-005)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import OWNER_PASSWORD, OWNER_USERNAME
from projectbudget.persistence.models import LoginAttempt, LoginSession
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import auth

NOW = datetime(2026, 11, 7, 12, 0, 0)
IDLE = timedelta(hours=1)
KEYS = ["user:owner", "ip:203.0.113.9"]


def _failures(session_factory: SessionFactory, key: str) -> int:
    with session_factory() as db:
        row = db.get(LoginAttempt, key)
    assert row is not None, key
    return row.failures


def test_concurrent_failures_are_all_counted(
    session_factory: SessionFactory, owner: auth.OwnerInfo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel attempts must not lose increments (the counter is one atomic UPDATE)."""
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILURES", 10_000)
    attempts = 24

    def attempt(_: int) -> auth.OwnerInfo | None:
        return auth.authenticate(session_factory, OWNER_USERNAME, "wrong", limit_keys=KEYS, now=NOW)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(attempts)))
    assert results == [None] * attempts
    assert _failures(session_factory, "user:owner") == attempts
    assert _failures(session_factory, "ip:203.0.113.9") == attempts


def test_concurrent_first_failures_create_one_bucket(
    session_factory: SessionFactory, owner: auth.OwnerInfo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The very first attempts race to create the rows; exactly one row per key must win."""
    monkeypatch.setattr(auth, "LOGIN_MAX_FAILURES", 10_000)
    keys = [f"user:race-{i % 3}" for i in range(12)]

    def attempt(key: str) -> None:
        auth.authenticate(session_factory, "nobody", "wrong", limit_keys=[key, "ip:race"], now=NOW)

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(attempt, keys))
    assert _failures(session_factory, "ip:race") == 12
    assert [_failures(session_factory, f"user:race-{i}") for i in range(3)] == [4, 4, 4]


def test_create_owner_normalizes_username_and_is_unique(session_factory: SessionFactory) -> None:
    created = auth.create_owner(session_factory, "  Owner ", OWNER_PASSWORD, now=NOW)
    assert created.username == "owner"
    assert auth.owner_exists(session_factory)
    with pytest.raises(auth.OwnerExistsError):
        auth.create_owner(session_factory, "second", OWNER_PASSWORD)


@pytest.mark.parametrize("username", ["", "   ", "has space", "x" * 101])
def test_invalid_usernames_are_rejected(session_factory: SessionFactory, username: str) -> None:
    with pytest.raises(auth.InvalidUsernameError):
        auth.create_owner(session_factory, username, OWNER_PASSWORD)
    assert not auth.owner_exists(session_factory)


def test_authenticate_success_and_failure(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    assert auth.authenticate(
        session_factory, "OWNER", OWNER_PASSWORD, limit_keys=KEYS, now=NOW
    ) == auth.OwnerInfo(id=owner.id, username=OWNER_USERNAME)
    assert auth.authenticate(session_factory, OWNER_USERNAME, "wrong", limit_keys=KEYS) is None
    assert auth.authenticate(session_factory, "nobody", OWNER_PASSWORD, limit_keys=KEYS) is None
    assert auth.authenticate(session_factory, "", OWNER_PASSWORD, limit_keys=KEYS) is None


def test_failures_lock_every_bucket_and_success_clears_them(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    for _ in range(auth.LOGIN_MAX_FAILURES):
        assert (
            auth.authenticate(session_factory, OWNER_USERNAME, "wrong", limit_keys=KEYS, now=NOW)
            is None
        )
    # Locked for the username and for the address, even with the right password.
    with pytest.raises(auth.LoginLockedError) as excinfo:
        auth.authenticate(session_factory, OWNER_USERNAME, OWNER_PASSWORD, limit_keys=KEYS, now=NOW)
    assert excinfo.value.retry_after == auth.LOGIN_LOCKOUT
    with pytest.raises(auth.LoginLockedError):
        auth.authenticate(
            session_factory, "other", OWNER_PASSWORD, limit_keys=["ip:203.0.113.9"], now=NOW
        )
    # A different address and a different username are unaffected.
    assert (
        auth.authenticate(
            session_factory,
            "other",
            OWNER_PASSWORD,
            limit_keys=["user:other", "ip:198.51.100.1"],
            now=NOW,
        )
        is None
    )

    later = NOW + auth.LOGIN_LOCKOUT + timedelta(seconds=1)
    assert (
        auth.authenticate(
            session_factory, OWNER_USERNAME, OWNER_PASSWORD, limit_keys=KEYS, now=later
        )
        is not None
    )
    with session_factory() as db:
        remaining = db.scalars(select(LoginAttempt.key).where(LoginAttempt.key.in_(KEYS))).all()
    assert remaining == []


def test_failure_window_resets_the_count(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    for _ in range(auth.LOGIN_MAX_FAILURES - 1):
        auth.authenticate(session_factory, OWNER_USERNAME, "wrong", limit_keys=KEYS, now=NOW)
    after_window = NOW + auth.LOGIN_WINDOW
    auth.authenticate(session_factory, OWNER_USERNAME, "wrong", limit_keys=KEYS, now=after_window)
    with session_factory() as db:
        row = db.get(LoginAttempt, "user:owner")
    assert row is not None
    assert row.failures == 1
    assert row.locked_until is None


def test_session_lifecycle(session_factory: SessionFactory, owner: auth.OwnerInfo) -> None:
    created = auth.create_session(session_factory, owner.id, now=NOW)
    assert created.expires_at == NOW + auth.SESSION_MAX_AGE

    resolved = auth.resolve_session(
        session_factory, created.cookie_value, idle_timeout=IDLE, now=NOW
    )
    assert resolved is not None
    assert resolved.principal.username == OWNER_USERNAME
    assert resolved.principal.auth == "session"
    assert resolved.csrf_token == created.csrf_token
    # The cookie value never touches the database; only its hash does.
    with session_factory() as db:
        ids = db.scalars(select(LoginSession.id)).all()
    assert ids == [auth.hash_secret(created.cookie_value)]
    assert created.cookie_value not in ids

    assert (
        auth.resolve_session(session_factory, "not-a-session", idle_timeout=IDLE, now=NOW) is None
    )

    auth.revoke_session(session_factory, resolved.id)
    assert (
        auth.resolve_session(session_factory, created.cookie_value, idle_timeout=IDLE, now=NOW)
        is None
    )


def test_idle_sessions_expire_and_active_ones_are_touched(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    created = auth.create_session(session_factory, owner.id, now=NOW)
    active = NOW + auth.SESSION_TOUCH_INTERVAL
    assert auth.resolve_session(
        session_factory, created.cookie_value, idle_timeout=IDLE, now=active
    )
    with session_factory() as db:
        row = db.get(LoginSession, auth.hash_secret(created.cookie_value))
    assert row is not None
    assert row.last_seen_at == active

    idle = active + IDLE
    assert (
        auth.resolve_session(session_factory, created.cookie_value, idle_timeout=IDLE, now=idle)
        is None
    )
    with session_factory() as db:
        assert db.get(LoginSession, auth.hash_secret(created.cookie_value)) is None


def test_sessions_have_an_absolute_lifetime(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    created = auth.create_session(session_factory, owner.id, now=NOW)
    end = NOW + auth.SESSION_MAX_AGE
    forever = timedelta(days=3650)
    assert (
        auth.resolve_session(session_factory, created.cookie_value, idle_timeout=forever, now=end)
        is None
    )
    # Expired rows are swept when the next session is created.
    stale = auth.create_session(session_factory, owner.id, now=NOW)
    auth.create_session(session_factory, owner.id, now=end)
    with session_factory() as db:
        assert db.get(LoginSession, auth.hash_secret(stale.cookie_value)) is None
