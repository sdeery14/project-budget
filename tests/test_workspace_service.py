"""The workspace currency: set at init, defaulted lazily, validated (ADR-003)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sqlalchemy import func, select

from projectbudget.domain.money import InvalidCurrencyError
from projectbudget.persistence.models import WorkspaceSettings
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace

NOW = datetime(2026, 11, 8, 9, 0, 0)


def test_default_currency_is_created_on_first_read(session_factory: SessionFactory) -> None:
    assert workspace.get_currency(session_factory) == "USD"
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceSettings)) == 1


def test_set_currency_normalises_and_overwrites(session_factory: SessionFactory) -> None:
    assert workspace.set_currency(session_factory, "eur", now=NOW) == "EUR"
    assert workspace.get_currency(session_factory) == "EUR"
    assert workspace.set_currency(session_factory, "GBP", now=NOW) == "GBP"
    assert workspace.get_currency(session_factory) == "GBP"
    with pytest.raises(InvalidCurrencyError):
        workspace.set_currency(session_factory, "dollars")


def test_concurrent_first_reads_create_one_row(session_factory: SessionFactory) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: workspace.get_currency(session_factory), range(16)))
    assert results == ["USD"] * 16
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceSettings)) == 1
