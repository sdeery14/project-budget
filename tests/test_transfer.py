"""Export and import of the whole workspace (ADR-008): round trip, ids, wipe, validation."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest

from projectbudget.adapters import export_format as fmt
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import (
    backlog,
    catalog,
    forecast,
    inventory,
    purchases,
    transfer,
    workspace,
)
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 8, 9, 0, 0)
AS_OF = date(2026, 11, 1)
TODAY = date(2026, 11, 8)


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    seeded = seed_canonical(session_factory, principal, now=NOW, as_of=AS_OF)
    # Make the workspace richer than the seed: a merge redirect, a partly received order, consumption.
    dupe = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Bar clamp (dupe)"), now=NOW
    )
    catalog.update_item(
        session_factory,
        principal,
        dupe.id,
        catalog.CatalogItemUpdate(kind=seeded.clamp.kind),
        now=NOW,
    )
    catalog.merge_items(session_factory, principal, dupe.id, seeded.clamp.id, now=NOW)
    order = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(seeded.bolts.id, 6, Money(45, "USD")),),
            status="ordered",  # type: ignore[arg-type]
            ordered_on=date(2026, 11, 3),
            vendor="Bolt Barn",
            shipping=Money(300, "USD"),
        ),
        now=NOW,
    )
    purchases.receive_line(
        session_factory, principal, order.lines[0].id, 2, occurred_on=date(2026, 11, 4), now=NOW
    )
    inventory.consume(
        session_factory,
        principal,
        seeded.workbench_bolts.id,
        3,
        occurred_on=date(2026, 11, 5),
        now=NOW,
    )
    return seeded


def _views(session_factory: SessionFactory, principal: Principal) -> tuple[str, str]:
    """Backlog and forecast as JSON under a frozen clock, the way the API renders them."""
    from projectbudget.api.schemas import BacklogOut, ForecastOut

    result = forecast.get_forecast(session_factory, principal, today=TODAY)
    assert result is not None
    return (
        BacklogOut.of(backlog.get_backlog(session_factory, principal)).model_dump_json(),
        ForecastOut.of(result).model_dump_json(),
    )


def test_export_then_wipe_then_import_reproduces_backlog_and_forecast_byte_for_byte(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    before_backlog, before_forecast = _views(session_factory, principal)
    document = transfer.export_workspace(session_factory, principal, now=NOW)
    text = document.to_json()
    assert text.endswith("\n")
    assert fmt.parse_document(text) == document  # the text form is lossless

    transfer.wipe_workspace(session_factory, principal)
    assert backlog.get_backlog(session_factory, principal).rows == ()
    assert catalog.list_items(session_factory, principal, include_merged=True) == []
    assert purchases.list_purchases(session_factory, principal) == []
    assert workspace.get_currency(session_factory) == "USD"  # settings survive a wipe

    summary = transfer.import_workspace(
        session_factory, principal, fmt.parse_document(text), now=NOW
    )
    assert summary.projects == 2
    assert summary.catalog_items == 5  # four live items plus the merged redirect
    assert summary.purchases == 1
    assert summary.purchase_lines == 1
    assert summary.budget_plan is True
    assert summary.replaced is False

    after_backlog, after_forecast = _views(session_factory, principal)
    assert after_backlog == before_backlog
    assert after_forecast == before_forecast
    # Every id came back: the fixture's handles still resolve to the same records.
    assert catalog.get_item(session_factory, principal, canonical.clamp.id).slug == "bar-clamp-600"
    assert inventory.consumed(session_factory, principal, canonical.workbench_bolts.id) == 3
    # And a second export of the restored workspace is identical apart from its timestamp.
    again = transfer.export_workspace(session_factory, principal, now=NOW)
    assert again.content_without_timestamp() == document.content_without_timestamp()


def test_import_refuses_a_non_empty_workspace_unless_replace(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    document = transfer.export_workspace(session_factory, principal, now=NOW)
    with pytest.raises(transfer.WorkspaceNotEmptyError):
        transfer.import_workspace(session_factory, principal, document)
    # Nothing changed.
    assert len(catalog.list_items(session_factory, principal, include_merged=True)) == 5

    purchases.mark_purchased(session_factory, principal, canonical.vise.id, 1, Money(8900, "USD"))
    summary = transfer.import_workspace(session_factory, principal, document, replace=True)
    assert summary.replaced is True
    assert len(purchases.list_purchases(session_factory, principal)) == 1  # the extra one is gone


def test_import_is_atomic_on_conflicting_ids(
    session_factory: SessionFactory, principal: Principal, canonical: CanonicalWorkspace
) -> None:
    document = transfer.export_workspace(session_factory, principal, now=NOW)
    broken = document.model_copy(
        update={"requirements": [*document.requirements, document.requirements[0]]}
    )
    transfer.wipe_workspace(session_factory, principal)
    with pytest.raises(transfer.ImportConflictError):
        transfer.import_workspace(session_factory, principal, broken)
    assert catalog.list_items(session_factory, principal, include_merged=True) == []
    dangling = document.model_copy(
        update={"backlog_order": [*document.backlog_order, "not-an-item"]}
    )
    with pytest.raises(transfer.ImportConflictError):
        transfer.import_workspace(session_factory, principal, dangling)
    assert catalog.list_items(session_factory, principal, include_merged=True) == []


def test_import_sets_the_currency_and_an_empty_export_round_trips(
    session_factory: SessionFactory, principal: Principal
) -> None:
    empty = transfer.export_workspace(session_factory, principal, now=NOW)
    assert empty.projects == []
    assert empty.budget_plan is None
    eur = empty.model_copy(update={"currency": "EUR"})
    transfer.import_workspace(session_factory, principal, eur)
    assert workspace.get_currency(session_factory) == "EUR"


def test_document_validation() -> None:
    with pytest.raises(fmt.InvalidDocumentError, match="Not JSON"):
        fmt.parse_document(b"{not json")
    with pytest.raises(fmt.InvalidDocumentError, match="JSON object"):
        fmt.parse_document("[]")
    with pytest.raises(fmt.InvalidDocumentError, match="Not a ProjectBudget export"):
        fmt.parse_document({"format": "something-else", "version": 1})
    with pytest.raises(fmt.InvalidDocumentError, match="version"):
        fmt.parse_document({"format": fmt.FORMAT, "version": 99})
    minimal = {
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "exported_at": "2026-11-08T09:00:00",
        "currency": "USD",
        "projects": [],
        "catalog_items": [],
        "requirements": [],
        "price_estimates": [],
        "inventory_movements": [],
        "purchases": [],
        "backlog_order": [],
        "budget_plan": None,
    }
    assert fmt.parse_document(json.dumps(minimal)).currency == "USD"
    with pytest.raises(fmt.InvalidDocumentError, match="Invalid export at projects"):
        fmt.parse_document({**minimal, "projects": [{"id": "x"}]})
    with pytest.raises(fmt.InvalidDocumentError, match="Extra inputs"):
        fmt.parse_document({**minimal, "surprise": 1})


def test_read_only_principals_can_export_but_not_import_or_wipe(
    session_factory: SessionFactory, principal: Principal, read_only_principal: Principal
) -> None:
    document = transfer.export_workspace(session_factory, read_only_principal, now=NOW)
    with pytest.raises(ReadOnlyPrincipalError):
        transfer.import_workspace(session_factory, read_only_principal, document)
    with pytest.raises(ReadOnlyPrincipalError):
        transfer.wipe_workspace(session_factory, read_only_principal)


def test_export_reads_one_consistent_snapshot_while_stock_moves(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt committed half-way through an export must not appear in some tables only."""
    order = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(canonical.vise.id, 1, Money(8900, "USD")),),
            status="ordered",  # type: ignore[arg-type]
        ),
    )
    movements_read = threading.Event()
    receipt_done = threading.Event()

    def pause(table: str) -> None:
        if table == "inventory_movements":
            movements_read.set()
            receipt_done.wait(timeout=10)

    monkeypatch.setattr(transfer, "_between_reads", pause)

    def receive() -> None:
        movements_read.wait(timeout=10)
        purchases.receive_line(session_factory, principal, order.lines[0].id, 1)
        receipt_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        exporting = pool.submit(transfer.export_workspace, session_factory, principal, now=NOW)
        receiving = pool.submit(receive)
        document = exporting.result(timeout=30)
        receiving.result(timeout=30)

    line = next(
        line for p in document.purchases for line in p.lines if line.id == order.lines[0].id
    )
    received_movements = [
        m for m in document.inventory_movements if m.purchase_line_id == order.lines[0].id
    ]
    # Either the whole receipt is in the export or none of it; the line and its movement agree.
    assert line.received_qty == sum(m.delta_qty for m in received_movements)
    assert line.received_qty == 0  # the snapshot predates the receipt
    # The database itself did take the receipt.
    assert purchases.get_purchase(session_factory, principal, order.id).lines[0].received_qty == 1
