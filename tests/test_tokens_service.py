"""API tokens: shown once, stored hashed, revocable, never self-managing (ADR-005)."""

from datetime import datetime

import pytest
from sqlalchemy import select

from projectbudget.domain.principal import Principal
from projectbudget.persistence.models import ApiToken
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import auth, tokens

NOW = datetime(2026, 11, 7, 12, 0, 0)


def session_principal(owner: auth.OwnerInfo) -> Principal:
    return Principal(owner_id=owner.id, username=owner.username, auth="session")


def test_issue_stores_only_the_hash(session_factory: SessionFactory, owner: auth.OwnerInfo) -> None:
    issued = tokens.issue_token(
        session_factory, session_principal(owner), " backup script ", read_only=False, now=NOW
    )
    assert issued.secret.startswith(tokens.API_KEY_PREFIX)
    assert issued.info.name == "backup script"
    assert issued.info.active
    with session_factory() as db:
        stored = db.scalars(select(ApiToken.token_hash)).all()
    assert stored == [auth.hash_secret(issued.secret)]
    assert issued.secret not in stored


def test_resolve_revoke_and_list(session_factory: SessionFactory, owner: auth.OwnerInfo) -> None:
    me = session_principal(owner)
    rw = tokens.issue_token(session_factory, me, "rw", read_only=False, now=NOW)
    ro = tokens.issue_token(session_factory, me, "ro", read_only=True, now=NOW)

    principal = tokens.resolve_token(session_factory, ro.secret, now=NOW)
    assert principal == Principal(
        owner_id=owner.id, username=owner.username, auth="token", read_only=True
    )
    assert tokens.resolve_token(session_factory, rw.secret, now=NOW) is not None
    assert tokens.resolve_token(session_factory, "pb_nope", now=NOW) is None

    listed = tokens.list_tokens(session_factory, me)
    assert [t.name for t in listed] == sorted(
        ["rw", "ro"], key=lambda n: [t.name for t in listed].index(n)
    )
    assert all(t.last_used_at == NOW for t in listed)

    revoked = tokens.revoke_token(session_factory, me, ro.info.id, now=NOW)
    assert revoked is not None
    assert revoked.revoked_at == NOW
    assert tokens.resolve_token(session_factory, ro.secret, now=NOW) is None
    assert tokens.resolve_token(session_factory, rw.secret, now=NOW) is not None
    assert tokens.revoke_token(session_factory, me, "does-not-exist") is None


def test_tokens_cannot_manage_tokens(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    me = session_principal(owner)
    issued = tokens.issue_token(session_factory, me, "rw", read_only=False)
    via_token = tokens.resolve_token(session_factory, issued.secret)
    assert via_token is not None
    with pytest.raises(tokens.TokenManagementRequiresSessionError):
        tokens.issue_token(session_factory, via_token, "another", read_only=False)
    with pytest.raises(tokens.TokenManagementRequiresSessionError):
        tokens.revoke_token(session_factory, via_token, issued.info.id)


def test_another_owner_cannot_revoke(
    session_factory: SessionFactory, owner: auth.OwnerInfo
) -> None:
    issued = tokens.issue_token(session_factory, session_principal(owner), "rw", read_only=False)
    stranger = Principal(owner_id="someone-else", username="stranger", auth="session")
    assert tokens.revoke_token(session_factory, stranger, issued.info.id) is None
    assert tokens.resolve_token(session_factory, issued.secret) is not None


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_invalid_names_are_rejected(
    session_factory: SessionFactory, owner: auth.OwnerInfo, name: str
) -> None:
    with pytest.raises(tokens.InvalidTokenNameError):
        tokens.issue_token(session_factory, session_principal(owner), name, read_only=False)
