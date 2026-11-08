"""RFC v3 §2 acceptance tests 1 to 4a (Milestone 1 and 2 gates), on the canonical fixture.

Each is checked three ways: the service result, the JSON view and the HTML page,
so the number a user sees is the number the API returns is the number the
domain computed.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, forecast, purchases


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
