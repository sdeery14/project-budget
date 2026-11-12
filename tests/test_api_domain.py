"""The JSON twins under /api/v1: CRUD, money as strings, and error mapping (ADR-002, ADR-003)."""

from typing import Any

from fastapi.testclient import TestClient

from conftest import csrf_token_from, make_client
from projectbudget.settings import Settings


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token_from(client.get("/projects").text)}


def _post(client: TestClient, path: str, body: dict[str, Any], expected: int = 201) -> Any:
    response = client.post(path, json=body, headers=_csrf(client))
    assert response.status_code == expected, (path, response.text)
    return response.json() if response.content else None


def test_project_requirement_estimate_round_trip(logged_in: TestClient) -> None:
    project = _post(logged_in, "/api/v1/projects", {"name": " Workbench ", "notes": "garage"})
    assert project["name"] == "Workbench"
    assert project["archived"] is False
    assert logged_in.get("/api/v1/projects").json() == [project]

    item = _post(
        logged_in,
        "/api/v1/catalog/items",
        {"name": "Bar clamp", "kind": "reusable", "unit": "each"},
    )
    assert item["slug"] == "bar-clamp"
    assert item["review_state"] == "confirmed"

    # Free text creates an unreviewed consumable; the typed unit must match.
    req = _post(
        logged_in,
        "/api/v1/requirements",
        {
            "project_id": project["id"],
            "qty": 10,
            "item_text": "M8 bolt",
            "unit": "each",
            "priority": 3,
        },
    )
    assert req["intent"] == "active"
    assert req["sharing"] == "shared"
    assert req["source_id"] is None  # entered by hand, not managed by a manifest
    bolt = logged_in.get(f"/api/v1/catalog/items/{req['catalog_item_id']}").json()
    assert bolt["review_state"] == "unreviewed"
    listed = logged_in.get(f"/api/v1/projects/{project['id']}/requirements").json()
    assert [r["id"] for r in listed] == [req["id"]]

    # Money in as a decimal string, out as string plus minor units plus currency.
    estimate = _post(
        logged_in,
        f"/api/v1/catalog/items/{item['id']}/estimates",
        {"price": "12.50", "as_of": "2026-11-01", "confidence": "high"},
    )
    assert estimate["price"] == {"amount": "12.50", "minor": 1250, "currency": "USD"}
    assert logged_in.get(f"/api/v1/catalog/items/{item['id']}/estimates/current").json() == estimate
    assert logged_in.get(f"/api/v1/catalog/items/{bolt['id']}/estimates/current").json() is None

    patched = logged_in.patch(
        f"/api/v1/requirements/{req['id']}",
        json={"qty": 12, "priority": 1},
        headers=_csrf(logged_in),
    ).json()
    assert (patched["qty"], patched["priority"]) == (12, 1)
    paused = _post(logged_in, f"/api/v1/requirements/{req['id']}/intent", {"intent": "paused"}, 200)
    assert paused["intent"] == "paused"
    archived = logged_in.patch(
        f"/api/v1/projects/{project['id']}", json={"archived": True}, headers=_csrf(logged_in)
    ).json()
    assert archived["archived"] is True
    assert logged_in.get("/api/v1/projects").json() == []
    assert logged_in.get("/api/v1/projects?include_archived=true").json()[0]["id"] == project["id"]


def test_stock_adjustments_and_consumption(logged_in: TestClient) -> None:
    project = _post(logged_in, "/api/v1/projects", {"name": "Shed"})
    req = _post(
        logged_in,
        "/api/v1/requirements",
        {"project_id": project["id"], "qty": 4, "item_text": "Hinge"},
    )
    item_id = req["catalog_item_id"]
    moved = _post(
        logged_in,
        f"/api/v1/catalog/items/{item_id}/adjustments",
        {"delta": 6, "note": "stock take", "occurred_on": "2026-11-08"},
    )
    assert moved["reason"] == "adjustment"
    used = _post(logged_in, f"/api/v1/requirements/{req['id']}/consume", {"qty": 3})
    assert (used["reason"], used["delta_qty"], used["requirement_id"]) == (
        "consumed",
        -3,
        req["id"],
    )
    movements = logged_in.get(f"/api/v1/catalog/items/{item_id}/movements").json()
    assert sorted(m["delta_qty"] for m in movements) == [-3, 6]
    backlog = logged_in.get("/api/v1/backlog").json()
    # 4 needed - 3 consumed = 1 remaining, 3 on hand: nothing left to buy, row leaves.
    assert backlog["rows"] == []
    assert backlog["unpriced"] == []


def test_money_must_be_a_decimal_string(logged_in: TestClient) -> None:
    item = _post(logged_in, "/api/v1/catalog/items", {"name": "Clamp"})
    path = f"/api/v1/catalog/items/{item['id']}/estimates"
    for price in (12.5, 1250, "12.345", "abc", "-1.00", "12.50 EUR"):
        response = logged_in.post(path, json={"price": price}, headers=_csrf(logged_in))
        assert response.status_code == 422, (price, response.text)
    assert logged_in.get(path).json() == []


def test_error_mapping(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    assert logged_in.get("/api/v1/projects/nope").status_code == 404
    assert logged_in.get("/api/v1/catalog/items/nope").status_code == 404
    assert logged_in.get("/api/v1/requirements/nope").status_code == 404
    assert logged_in.get("/api/v1/projects/nope/requirements").status_code == 404

    project = _post(logged_in, "/api/v1/projects", {"name": "Shed"})
    _post(logged_in, "/api/v1/catalog/items", {"name": "Clamp", "slug": "clamp"})
    dup = logged_in.post(
        "/api/v1/catalog/items", json={"name": "Other", "slug": "clamp"}, headers=headers
    )
    assert dup.status_code == 409
    assert "already used" in dup.json()["detail"]

    bad_slug = logged_in.post(
        "/api/v1/catalog/items", json={"name": "X", "slug": "Not A Slug"}, headers=headers
    )
    assert bad_slug.status_code == 422

    both = logged_in.post(
        "/api/v1/requirements",
        json={"project_id": project["id"], "qty": 1, "catalog_item_id": "x", "item_text": "y"},
        headers=headers,
    )
    assert both.status_code == 422
    zero = logged_in.post(
        "/api/v1/requirements",
        json={"project_id": project["id"], "qty": 0, "item_text": "Bolt"},
        headers=headers,
    )
    assert zero.status_code == 422

    shared = _post(
        logged_in,
        "/api/v1/requirements",
        {"project_id": project["id"], "qty": 1, "catalog_item_id": _item_id(logged_in, "clamp")},
    )
    logged_in.patch(
        f"/api/v1/catalog/items/{shared['catalog_item_id']}",
        json={"kind": "reusable"},
        headers=headers,
    )
    _post(logged_in, f"/api/v1/catalog/items/{shared['catalog_item_id']}/adjustments", {"delta": 1})
    refused = logged_in.post(
        f"/api/v1/requirements/{shared['id']}/consume", json={"qty": 1}, headers=headers
    )
    assert refused.status_code == 409
    assert "shared reusable" in refused.json()["detail"]

    order = logged_in.put("/api/v1/backlog/order", json={"item_ids": ["nope"]}, headers=headers)
    assert order.status_code == 404
    duplicate = logged_in.put(
        "/api/v1/backlog/order",
        json={"item_ids": [shared["catalog_item_id"], shared["catalog_item_id"]]},
        headers=headers,
    )
    assert duplicate.status_code == 422


def test_read_only_token_reads_everything_and_writes_nothing(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    project = _post(logged_in, "/api/v1/projects", {"name": "Shed"})
    token = logged_in.post(
        "/api/v1/tokens", json={"name": "ro", "read_only": True}, headers=headers
    ).json()["token"]
    settings: Settings = logged_in.app.state.settings  # type: ignore[attr-defined]
    with make_client(settings) as api:
        bearer = {"Authorization": f"Bearer {token}"}
        assert api.get("/api/v1/backlog", headers=bearer).status_code == 200
        assert api.get("/api/v1/projects", headers=bearer).json()[0]["id"] == project["id"]
        assert api.post("/api/v1/projects", json={"name": "No"}, headers=bearer).status_code == 403
        assert (
            api.put("/api/v1/backlog/order", json={"item_ids": []}, headers=bearer).status_code
            == 403
        )


def _item_id(client: TestClient, slug: str) -> str:
    items = client.get("/api/v1/catalog/items").json()
    return str(next(item["id"] for item in items if item["slug"] == slug))
