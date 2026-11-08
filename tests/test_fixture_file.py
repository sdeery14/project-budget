"""``tests/fixtures/workspace.json`` is an export of the canonical workspace and imports cleanly (ADR-008)."""

from datetime import date
from pathlib import Path

from projectbudget.adapters.export_format import parse_document
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, forecast, transfer

FIXTURE = Path(__file__).parent / "fixtures" / "workspace.json"


def test_fixture_file_imports_and_reproduces_the_acceptance_numbers(
    session_factory: SessionFactory, principal: Principal
) -> None:
    document = parse_document(FIXTURE.read_bytes())
    assert document.currency == "USD"
    summary = transfer.import_workspace(session_factory, principal, document)
    assert (summary.projects, summary.catalog_items, summary.requirements) == (2, 4, 6)
    assert summary.budget_plan

    result = backlog.get_backlog(session_factory, principal)
    by_slug = {row.item.slug: row for row in result.rows}
    assert by_slug["bar-clamp-600"].gross_shortfall == 2
    assert by_slug["m8-carriage-bolt-80"].gross_shortfall == 11
    assert result.unpriced_count == 1
    assert str(result.total) == "118.95"

    schedule = forecast.get_forecast(session_factory, principal, today=date(2026, 11, 8))
    assert schedule is not None
    assert str(schedule.conservative.opening_balance) == "70.00"

    # The file is the stable text form: re-exporting the imported workspace gives the same content.
    again = transfer.export_workspace(session_factory, principal, now=document.exported_at)
    assert again.to_json() == FIXTURE.read_text(encoding="utf-8")
