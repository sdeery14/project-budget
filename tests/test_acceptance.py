"""RFC v3 §2 acceptance tests 1 to 5 (Milestone 1 to 3 gates), on the canonical fixture.

Each is checked three ways: the service result, the JSON view and the HTML page,
so the number a user sees is the number the API returns is the number the
domain computed.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from projectbudget.domain.catalog import PurchaseStatus
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, forecast, purchases, transfer


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=date(2026, 11, 1))


def test_acceptance_1_two_projects_share_a_reusable_and_a_consumable(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    """A needs 3 and B needs 2 of a reusable; A needs 10 and B needs 5 of a consumable.

    With 1 reusable and 4 consumable owned, the backlog shows shortfall 2 and 11.
    """
    result = backlog.get_backlog(session_factory, principal)
    by_id = {row.item.id: row for row in result.rows}
    clamp, bolts = by_id[canonical.clamp.id], by_id[canonical.bolts.id]
    assert (clamp.needed, clamp.on_hand, clamp.gross_shortfall) == (3, 1, 2)
    assert (bolts.needed, bolts.on_hand, bolts.gross_shortfall) == (15, 4, 11)
    assert set(clamp.project_ids) == {canonical.workbench.id, canonical.garden_gate.id}

    api = logged_in.get("/api/v1/backlog").json()
    api_rows = {row["item_id"]: row for row in api["rows"]}
    assert api_rows[canonical.clamp.id]["gross_shortfall"] == 2
    assert api_rows[canonical.bolts.id]["gross_shortfall"] == 11
    assert api_rows[canonical.clamp.id]["shared"] is True

    page = logged_in.get("/backlog")
    assert page.status_code == 200
    clamp_row = _row_html(page.text, f'id="row-{canonical.clamp.id}"')
    bolts_row = _row_html(page.text, f'id="row-{canonical.bolts.id}"')
    assert "<strong>2</strong>" in clamp_row
    assert "<strong>11</strong>" in bolts_row
    assert "shared" in clamp_row


def test_acceptance_2_unpriced_item_is_bucketed_and_total_is_a_lower_bound(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    result = backlog.get_backlog(session_factory, principal)
    assert [row.item.id for row in result.unpriced] == [canonical.hinge_set.id]
    assert result.is_lower_bound
    # 2 clamps at 12.50 + 11 bolts at 0.45 + 1 vise at 89.00; the hinge set adds nothing.
    assert str(result.total) == "118.95"

    api = logged_in.get("/api/v1/backlog").json()
    assert api["lower_bound"] is True
    assert api["unpriced_count"] == 1
    assert api["total"] == {"amount": "118.95", "minor": 11895, "currency": "USD"}
    assert api["unpriced"][0]["item_id"] == canonical.hinge_set.id
    assert api["unpriced"][0]["line_total"] is None

    page = logged_in.get("/backlog").text
    assert "118.95 USD" in page
    assert "lower bound" in page
    assert "Needs a price" in page
    assert f'id="unpriced-{canonical.hinge_set.id}"' in page
    assert "1 item without a price" in page


def _row_html(html: str, marker: str) -> str:
    start = html.index(marker)
    return html[start : html.index("</tr>", start)]


def test_acceptance_3_the_timeline_explains_each_month(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    """Every month shows opening balance, contribution, purchases and closing balance."""
    result = forecast.get_forecast(session_factory, principal, today=date(2026, 11, 8))
    assert result is not None
    months = result.conservative.months
    explained = [
        (
            m.month,
            str(m.opening),
            str(m.contribution),
            [p.item.name for p in m.purchases],
            str(m.closing),
        )
        for m in months
    ]
    assert explained == [
        (date(2026, 11, 1), "70.00", "25.00", ["Bar clamp 600 mm"], "67.50"),
        (date(2026, 12, 1), "67.50", "25.00", [], "92.50"),
        (
            date(2027, 1, 1),
            "92.50",
            "25.00",
            ["Bench vise 125 mm", "M8 carriage bolt 80 mm"],
            "14.15",
        ),
    ]
    for month in months:
        assert (
            month.closing
            == month.opening + month.contribution + month.overrides_total - month.purchases_total
        )

    api = logged_in.get("/api/v1/forecast?today=2026-11-08").json()["conservative"]["months"]
    assert [
        (m["opening"]["amount"], m["contribution"]["amount"], m["closing"]["amount"]) for m in api
    ] == [
        ("70.00", "25.00", "67.50"),
        ("67.50", "25.00", "92.50"),
        ("92.50", "25.00", "14.15"),
    ]

    page = logged_in.get("/timeline?today=2026-11-08").text
    november = _row_html(page, 'id="month-2026-11-01"')
    for cell in ("70.00", "25.00", "Bar clamp 600 mm x 2: 27.50", "67.50"):
        assert cell in november, cell


def test_acceptance_4a_marking_purchased_removes_the_item_and_lowers_the_opening_balance(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    today = date(2026, 11, 8)
    before = forecast.get_forecast(session_factory, principal, today=today)
    assert before is not None
    assert before.conservative.opening_balance == Money(7000, "USD")
    assert canonical.clamp.id in {
        row.item.id for row in backlog.get_backlog(session_factory, principal).rows
    }

    purchases.mark_purchased(
        session_factory, principal, canonical.clamp.id, 2, Money(1250, "USD"), ordered_on=today
    )

    after = forecast.get_forecast(session_factory, principal, today=today)
    assert after is not None
    assert canonical.clamp.id not in {
        row.item.id for row in backlog.get_backlog(session_factory, principal).rows
    }
    assert canonical.clamp.id not in {item.item.item_id for item in after.conservative.items}
    assert after.recent_purchases_total == Money(2500, "USD")
    assert after.conservative.opening_balance == Money(4500, "USD")
    assert after.conservative.months[0].opening == Money(4500, "USD")

    api = logged_in.get("/api/v1/forecast?today=2026-11-08").json()
    assert api["conservative"]["opening_balance"]["amount"] == "45.00"
    assert canonical.clamp.id not in {i["item_id"] for i in api["conservative"]["items"]}
    assert canonical.clamp.id not in {
        r["item_id"] for r in logged_in.get("/api/v1/backlog").json()["rows"]
    }


def test_acceptance_4b_partial_receipt_leaves_the_remainder_on_order(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    """Order 2 clamps, receive 1: the backlog shows 1 in stock more, 1 still short, 1 on order."""
    order = purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(canonical.clamp.id, 2, Money(1250, "USD")),),
            status=PurchaseStatus.ORDERED,
            ordered_on=date(2026, 11, 8),
        ),
    )
    purchases.receive_line(
        session_factory, principal, order.lines[0].id, 1, occurred_on=date(2026, 11, 8)
    )
    row = next(
        r
        for r in backlog.get_backlog(session_factory, principal).rows
        if r.item.id == canonical.clamp.id
    )
    assert (row.needed, row.on_hand, row.gross_shortfall, row.on_order, row.net_shortfall) == (
        3,
        2,
        1,
        1,
        0,
    )
    api_row = next(
        r
        for r in logged_in.get("/api/v1/backlog").json()["rows"]
        if r["item_id"] == canonical.clamp.id
    )
    assert (api_row["gross_shortfall"], api_row["on_order"]) == (1, 1)
    # Money is net of the order (ADR-012, #25): nothing left to buy for the clamps, and the
    # one still on order is committed at the purchase's price, not the estimate.
    assert (api_row["line_total"]["amount"], api_row["fully_ordered"]) == ("0.00", True)
    assert api_row["on_order_cost"]["amount"] == "12.50"
    api = logged_in.get("/api/v1/backlog").json()
    assert api["committed"]["amount"] == "12.50"
    page = logged_in.get("/backlog").text
    clamp_row = _row_html(page, f'id="row-{canonical.clamp.id}"')
    cells = [c for c in clamp_row.split("<td") if 'class="num"' in c]
    assert "<strong>1</strong>" in clamp_row  # shortfall
    assert any(">1</td>" in c for c in cells)  # on order
    assert ">on order</span>" in clamp_row
    assert 'id="backlog-committed">12.50 USD' in page

    # The unpriced hinge set, fully ordered: nothing left to buy, so it needs no price now.
    purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(canonical.hinge_set.id, 1, Money(2000, "USD")),),
            status=PurchaseStatus.ORDERED,
            ordered_on=date(2026, 11, 8),
        ),
    )
    result = backlog.get_backlog(session_factory, principal)
    assert result.unpriced == ()
    assert not result.is_lower_bound
    assert result.committed == Money(3250, "USD")
    page = logged_in.get("/backlog").text
    hinge_row = _row_html(page, f'id="row-{canonical.hinge_set.id}"')
    assert ">on order</span>" in hinge_row
    assert ">0.00</td>" in hinge_row
    assert "—" in hinge_row  # no unit price to show
    assert 'id="unpriced-count">0<' in page
    assert 'id="backlog-committed">32.50 USD' in page
    # Net shortfall 0: the forecast schedules nothing for the clamps while the order is open.
    result = forecast.get_forecast(session_factory, principal, today=date(2026, 11, 8))
    assert result is not None
    assert canonical.clamp.id not in {i.item.item_id for i in result.conservative.items}


def test_acceptance_5_export_wipe_import_reproduces_backlog_and_forecast(
    session_factory: SessionFactory,
    principal: Principal,
    canonical: CanonicalWorkspace,
    logged_in: TestClient,
) -> None:
    """Byte-equal backlog and forecast JSON under a frozen clock, with every id preserved."""
    frozen = "2026-11-08"
    before_backlog = logged_in.get("/api/v1/backlog").text
    before_forecast = logged_in.get(f"/api/v1/forecast?today={frozen}").text
    document = transfer.export_workspace(session_factory, principal)

    transfer.wipe_workspace(session_factory, principal)
    assert logged_in.get("/api/v1/backlog").json()["rows"] == []
    assert logged_in.get(f"/api/v1/forecast?today={frozen}").status_code == 404

    transfer.import_workspace(session_factory, principal, document)
    assert logged_in.get("/api/v1/backlog").text == before_backlog
    assert logged_in.get(f"/api/v1/forecast?today={frozen}").text == before_forecast
    assert logged_in.get(f"/api/v1/projects/{canonical.workbench.id}").json()["name"] == "Workbench"
    assert logged_in.get(f"/api/v1/requirements/{canonical.gate_hinges.id}").status_code == 200
