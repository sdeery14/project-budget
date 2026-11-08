"""RFC v3 §2 acceptance tests 1 and 2 (Milestone 1 gate), on the canonical fixture.

Each is checked three ways: the service result, the JSON view and the HTML page,
so the number a user sees is the number the API returns is the number the
domain computed.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog


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
