"""Argon2id hashing (ADR-005)."""

import pytest

from projectbudget.services import passwords


def test_hash_is_argon2id_and_verifies() -> None:
    hashed = passwords.hash_password("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password(hashed, "a long enough password")
    assert not passwords.verify_password(hashed, "a long enough passwor")


def test_short_passwords_are_rejected() -> None:
    with pytest.raises(passwords.WeakPasswordError, match="at least"):
        passwords.hash_password("short")


def test_garbage_hash_does_not_verify() -> None:
    assert not passwords.verify_password("not a hash", "whatever password")


def test_verify_or_burn_without_hash_is_false_but_does_the_work() -> None:
    assert passwords.verify_or_burn(None, "whatever password") is False
    hashed = passwords.hash_password("a long enough password")
    assert passwords.verify_or_burn(hashed, "a long enough password") is True
