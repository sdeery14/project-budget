"""The budget and timeline pages: plan form, overrides, month-by-month table, mark purchased."""

import html
from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import clock
from projectbudget.web.templates import STATIC_DIR

AS_OF = date(2026, 11, 1)
TODAY = "2026-11-08"


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=AS_OF)


def _csrf(client: TestClient) -> str:
    return csrf_token_from(client.get("/budget").text)


def test_timeline_without_a_plan_points_to_the_budget_page(logged_in: TestClient) -> None:
    page = logged_in.get("/timeline")
    assert page.status_code == 200
    assert "needs a budget plan" in page.text
    assert 'href="/budget"' in page.text
    assert "chart.umd.js" not in page.text  # nothing to draw yet


def test_budget_page_creates_a_plan_and_overrides(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    empty = logged_in.get("/budget").text
    assert "No plan yet" in empty
    # Without a plan the balance date starts at today (#23); with one it shows the plan's date.
    balance_input = empty.split('id="balance_as_of"')[1].split(">")[0]
    assert f'value="{clock.today().isoformat()}"' in balance_input
    saved = logged_in.post(
        "/budget",
        data={
            "csrf_token": csrf,
            "earmarked_funds": "150.00",
            "balance_as_of": "2026-11-01",
            "protected_reserve": "80.00",
            "monthly_contribution": "25.00",
            "horizon_months": "12",
            "contingency_pct": "10",
        },
    )
    assert saved.status_code == 303
    page = logged_in.get("/budget").text
    assert 'value="150.00"' in page
    assert 'value="2026-11-01"' in page
    assert "Month overrides" in page

    added = logged_in.post(
        "/budget/overrides",
        data={"csrf_token": csrf, "month": "2026-12", "delta": "-120.00", "label": "Car insurance"},
    )
    assert added.status_code == 303
    page = logged_in.get("/budget").text
    assert "Dec 2026" in page
    assert "-120.00" in page
    assert "Car insurance" in page
    assert '<table id="overrides" class="responsive">' in page  # stacks at phone width
    assert '<td data-label="Label">Car insurance</td>' in page
    override_id = logged_in.get("/api/v1/budget/plan").json()["overrides"][0]["id"]
    removed = logged_in.post(f"/budget/overrides/{override_id}/delete", data={"csrf_token": csrf})
    assert removed.status_code == 303
    assert "Car insurance" not in logged_in.get("/budget").text

    bad = logged_in.post(
        "/budget",
        data={
            "csrf_token": csrf,
            "earmarked_funds": "lots",
            "protected_reserve": "0",
            "monthly_contribution": "0",
        },
    )
    assert bad.status_code == 422
    assert "Not a decimal amount" in bad.text
    bad_month = logged_in.post(
        "/budget/overrides",
        data={"csrf_token": csrf, "month": "soon", "delta": "1.00", "label": "x"},
    )
    assert bad_month.status_code == 422
    assert "Month must look like" in bad_month.text


def test_timeline_page_explains_each_month_and_draws_the_chart(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    page = logged_in.get(f"/timeline?today={TODAY}")
    assert page.status_code == 200
    text = page.text
    assert 'id="spendable-now">70.00 USD' in text
    assert 'id="protected-reserve">80.00 USD' in text
    assert 'id="blocked-banner"' not in text
    assert 'id="reserve-shortfall"' not in text
    assert 'id="month-2026-11-01"' in text
    assert 'id="month-2027-01-01"' in text
    assert "Bar clamp 600 mm x 2: 27.50" in text
    assert "Bench vise 125 mm x 1: 97.90" in text
    assert "Planning projection from your inputs; not financial advice." in text
    assert "optimistic" in text  # the unpriced P1 hinge set sits ahead
    assert "Dec 2026" in text
    assert "Jan 2027" in text
    # Chart data travels in data-* attributes; the scripts are vendored, never inline.
    # Jinja escapes the quotes inside the attribute; the browser decodes them before JSON.parse.
    assert 'data-conservative=\'["67.50", "92.50", "14.15"]\'' in html.unescape(text)
    assert "vendor/chartjs/chart.umd.js" in text
    assert "static/timeline.js" in text
    # #22: the caption says why the optimistic line can dip below the conservative one, and
    # the bars carry their items for the tooltip.
    assert 'id="chart-caption"' in text
    assert "affords a purchase sooner" in text
    assert "adds the 10 % contingency" in text
    assert (
        "data-purchase-items='"
        '[["Bar clamp 600 mm"], [], ["Bench vise 125 mm", "M8 carriage bolt 80 mm"]]\''
        in html.unescape(text)
    )
    assert (STATIC_DIR / "timeline.js").exists()
    assert logged_in.get("/static/vendor/chartjs/chart.umd.js").status_code == 200


def test_budget_page_says_how_to_offset_a_contribution_already_in_the_balance(
    logged_in: TestClient,
) -> None:
    text = logged_in.get("/budget").text
    assert 'id="contribution-help"' in text
    assert "<strong>minus</strong> the contribution" in text
    assert "already in balance" in text
    assert "after payday" not in text


def test_a_reserve_shortfall_is_a_note_not_a_red_negative(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    """#21: the owner's plan: balance 0, reserve 100, contribution 350."""
    csrf = csrf_token_from(logged_in.get("/budget").text)
    saved = logged_in.post(
        "/budget",
        data={
            "csrf_token": csrf,
            "earmarked_funds": "0.00",
            "balance_as_of": "2026-11-01",
            "protected_reserve": "100.00",
            "monthly_contribution": "350.00",
            "horizon_months": "12",
            "contingency_pct": "10",
        },
    )
    assert saved.status_code in (200, 303), saved.text
    text = logged_in.get(f"/timeline?today={TODAY}").text
    assert 'id="spendable-now">0.00 USD' in text
    assert 'class="value neg"' not in text
    assert 'class="money neg"' not in text  # the month table keeps the arithmetic, not the red
    assert "Below zero only by the reserve" in text
    assert 'id="reserve-shortfall">100.00 USD' in text
    assert "this month's contribution covers first" in text
    assert 'id="blocked-banner"' not in text
    assert "Bar clamp 600 mm x 2: 27.50" in text  # November buys

    # The same plan with a contribution that cannot cover the reserve this month: a plain
    # warning naming the first month that can buy, not an error that contradicts the table.
    logged_in.post(
        "/budget",
        data={
            "csrf_token": csrf,
            "earmarked_funds": "0.00",
            "balance_as_of": "2026-11-01",
            "protected_reserve": "100.00",
            "monthly_contribution": "60.00",
            "horizon_months": "12",
            "contingency_pct": "0",
        },
    )
    text = logged_in.get(f"/timeline?today={TODAY}").text
    assert 'id="blocked-banner"' in text
    assert "Nothing can be bought before December 2026" in text
    assert "the monthly contribution does not cover yet" in text


def test_mark_purchased_from_the_timeline(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    csrf = _csrf(logged_in)
    marked = logged_in.post(
        "/purchases/mark",
        data={
            "csrf_token": csrf,
            "item_id": canonical.clamp.id,
            "qty": "2",
            "unit_price": "12.50",
        },
    )
    assert marked.status_code == 303
    assert marked.headers["location"] == "/timeline"
    text = logged_in.get(f"/timeline?today={TODAY}").text
    assert f'id="item-{canonical.clamp.id}"' not in text
    assert "45.00 USD" in text  # 70.00 opening less the 25.00 just spent
    assert "25.00 USD" in text  # spent since balance_as_of

    bad = logged_in.post(
        "/purchases/mark",
        data={"csrf_token": csrf, "item_id": canonical.clamp.id, "qty": "1", "unit_price": "x"},
    )
    assert bad.status_code == 422
    assert "Not a decimal amount" in bad.text
