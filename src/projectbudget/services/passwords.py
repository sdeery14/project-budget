"""Argon2id password hashing (ADR-005). Nothing else in the app touches hashes."""

import functools
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 10

# Argon2id with the library defaults (RFC 9106 second recommended parameter set).
_hasher = PasswordHasher()


class WeakPasswordError(ValueError):
    """The password does not meet the minimum length."""


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        msg = f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        raise WeakPasswordError(msg)


def hash_password(password: str) -> str:
    validate_password(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def verify_or_burn(password_hash: str | None, password: str) -> bool:
    """Verify against the real hash, or do equivalent work when there is none.

    An unknown username must take as long as a wrong password, otherwise the
    response time reveals which usernames exist.
    """
    if password_hash is None:
        verify_password(_dummy_hash(), password)
        return False
    return verify_password(password_hash, password)


@functools.cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))
