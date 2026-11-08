"""JSON twins for the budget plan, purchases and the forecast (ADR-002, ADR-003, ADR-006)."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory

AS_OF = date(2026, 11, 1)
TODAY = "2026-11-08"


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token_from(client.get("/projects").text)}


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=AS_OF)


def test_plan_round_trip_and_overrides(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    assert logged_in.get("/api/v1/budget/plan").status_code == 404
    assert logged_in.get("/api/v1/forecast").status_code == 404

    saved = logged_in.put(
        "/api/v1/budget/plan",
        json={
            "earmarked_funds": "150.00",
            "protected_reserve": "80.00",
            "monthly_contribution": "25.00",
            "horizon_months": 12,
            "contingency_pct": 10,
            "balance_as_of": "2026-11-01",
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    plan = saved.json()
    assert plan["earmarked_funds"] == {"amount": "150.00", "minor": 15000, "currency": "USD"}
    assert plan["balance_as_of"] == "2026-11-01"
    assert logged_in.get("/api/v1/budget/plan").json() == plan

    bonus = logged_in.post(
        "/api/v1/budget/overrides",
        json={"month": "2026-12-15", "delta": "200.00", "label": "Bonus"},
        headers=headers,
    )
    assert bonus.status_code == 201, bonus.text
    assert bonus.json()["month"] == "2026-12-01"
    assert logged_in.get("/api/v1/budget/plan").json()["overrides"][0]["id"] == bonus.json()["id"]
    gone = logged_in.delete(f"/api/v1/budget/overrides/{bonus.json()['id']}", headers=headers)
    assert gone.status_code == 204
    assert logged_in.get("/api/v1/budget/plan").json()["overrides"] == []
    assert logged_in.delete("/api/v1/budget/overrides/nope", headers=headers).status_code == 404

    for body in (
        {"earmarked_funds": 150.0},  # floats are never money
        {"earmarked_funds": "150.00", "horizon_months": 0},
        {"earmarked_funds": "150.00", "contingency_pct": 101},
        {"earmarked_funds": "abc"},
    ):
        response = logged_in.put("/api/v1/budget/plan", json=body, headers=headers)
        assert response.status_code == 422, (body, response.text)


def test_forecast_json_explains_every_month(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    result = logged_in.get(f"/api/v1/forecast?today={TODAY}")
    assert result.status_code == 200, result.text
    body: dict[str, Any] = result.json()
    assert body["disclaimer"] == "Planning projection from your inputs; not financial advice."
    assert body["lower_bound"] is True
    assert body["unpriced_count"] == 1
    assert body["unpriced_ahead"] is True  # the P1 hinge set is unpriced
    assert body["balance_stale"] is False
    conservative = body["conservative"]
    assert conservative["opening_balance"]["amount"] == "70.00"
    months = conservative["months"]
    assert [m["month"] for m in months] == ["2026-11-01", "2026-12-01", "2027-01-01"]
    assert [m["closing"]["amount"] for m in months] == ["67.50", "92.50", "14.15"]
    assert [p["name"] for p in months[0]["purchases"]] == ["Bar clamp 600 mm"]
    assert months[0]["purchases"][0]["cost"]["amount"] == "27.50"
    assert [p["name"] for p in months[2]["purchases"]] == [
        "Bench vise 125 mm",
        "M8 carriage bolt 80 mm",
    ]
    items = {i["item_id"]: i for i in conservative["items"]}
    assert items[canonical.vise.id]["month"] == "2027-01-01"
    optimistic = {i["item_id"]: i for i in body["optimistic"]["items"]}
    assert optimistic[canonical.vise.id]["month"] == "2026-12-01"
    assert optimistic[canonical.vise.id]["cost"]["amount"] == "89.00"


def test_purchases_api_and_mark_purchased(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    headers = _csrf(logged_in)
    recorded = logged_in.post(
        "/api/v1/purchases",
        json={
            "vendor": "Tool Shop",
            "ordered_on": "2026-11-05",
            "status": "ordered",
            "shipping": "5.00",
            "lines": [{"catalog_item_id": canonical.bolts.id, "qty": 4, "unit_price": "0.45"}],
        },
        headers=headers,
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["total"]["amount"] == "6.80"
    assert recorded.json()["lines"][0]["received_qty"] == 0
    row = next(
        r
        for r in logged_in.get("/api/v1/backlog").json()["rows"]
        if r["item_id"] == canonical.bolts.id
    )
    assert (row["gross_shortfall"], row["on_order"], row["net_shortfall"]) == (11, 4, 7)

    marked = logged_in.post(
        "/api/v1/purchases/mark",
        json={"item_id": canonical.clamp.id, "qty": 2, "unit_price": "12.50"},
        headers=headers,
    )
    assert marked.status_code == 201, marked.text
    assert marked.json()["status"] == "received"
    listed = logged_in.get("/api/v1/purchases").json()
    assert {p["id"] for p in listed} == {recorded.json()["id"], marked.json()["id"]}
    assert logged_in.get(f"/api/v1/purchases/{marked.json()['id']}").json() == marked.json()
    assert logged_in.get("/api/v1/purchases/nope").status_code == 404

    bad = logged_in.post(
        "/api/v1/purchases/mark",
        json={"item_id": canonical.clamp.id, "qty": 1, "unit_price": 12.5},
        headers=headers,
    )
    assert bad.status_code == 422
    empty = logged_in.post("/api/v1/purchases", json={"lines": []}, headers=headers)
    assert empty.status_code == 422


def test_read_only_token_cannot_change_budget_or_buy(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    headers = _csrf(logged_in)
    token = logged_in.post(
        "/api/v1/tokens", json={"name": "ro", "read_only": True}, headers=headers
    ).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    assert logged_in.get("/api/v1/forecast", headers=bearer).status_code == 200
    assert logged_in.get("/api/v1/budget/plan", headers=bearer).status_code == 200
    assert (
        logged_in.put(
            "/api/v1/budget/plan", json={"earmarked_funds": "1.00"}, headers=bearer
        ).status_code
        == 403
    )
    assert (
        logged_in.post(
            "/api/v1/purchases/mark",
            json={"item_id": canonical.clamp.id, "qty": 1, "unit_price": "1.00"},
            headers=bearer,
        ).status_code
        == 403
    )
