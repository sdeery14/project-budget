"""JSON twins for the purchase lifecycle, catalog merge and export/import."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory

AS_OF = date(2026, 11, 1)


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token_from(client.get("/projects").text)}


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=AS_OF)


def test_purchase_lifecycle_over_the_api(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    headers = _csrf(logged_in)
    created = logged_in.post(
        "/api/v1/purchases",
        json={
            "status": "planned",
            "vendor": "Tool Shop",
            "ordered_on": "2026-11-05",
            "lines": [{"catalog_item_id": canonical.clamp.id, "qty": 2, "unit_price": "12.50"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    purchase = created.json()
    pid = purchase["id"]

    ordered = logged_in.post(
        f"/api/v1/purchases/{pid}/status", json={"status": "ordered"}, headers=headers
    )
    assert ordered.json()["status"] == "ordered"
    row = next(
        r
        for r in logged_in.get("/api/v1/backlog").json()["rows"]
        if r["item_id"] == canonical.clamp.id
    )
    assert (row["on_order"], row["net_shortfall"]) == (2, 0)

    grown = logged_in.post(
        f"/api/v1/purchases/{pid}/lines",
        json={"catalog_item_id": canonical.bolts.id, "qty": 4, "unit_price": "0.45"},
        headers=headers,
    )
    assert grown.status_code == 201
    assert len(grown.json()["lines"]) == 2
    assert grown.json()["total"]["amount"] == "26.80"

    edited = logged_in.patch(
        f"/api/v1/purchases/{pid}",
        json={"shipping": "5.00", "vendor": "Tool Shop Ltd"},
        headers=headers,
    )
    assert edited.json()["total"]["amount"] == "31.80"
    assert edited.json()["vendor"] == "Tool Shop Ltd"

    clamp_line = grown.json()["lines"][0]["id"]
    partial = logged_in.post(
        f"/api/v1/purchases/{pid}/lines/{clamp_line}/receive",
        json={"qty": 1, "occurred_on": "2026-11-07"},
        headers=headers,
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["lines"][0]["received_qty"] == 1
    assert partial.json()["status"] == "ordered"
    row = next(
        r
        for r in logged_in.get("/api/v1/backlog").json()["rows"]
        if r["item_id"] == canonical.clamp.id
    )
    assert (row["on_hand"], row["gross_shortfall"], row["on_order"]) == (2, 1, 1)

    bolt_line = grown.json()["lines"][1]["id"]
    removed = logged_in.delete(f"/api/v1/purchases/{pid}/lines/{bolt_line}", headers=headers)
    assert removed.status_code == 200
    assert len(removed.json()["lines"]) == 1

    done = logged_in.post(f"/api/v1/purchases/{pid}/receive", json={}, headers=headers)
    assert done.json()["status"] == "received"
    assert done.json()["lines"][0]["received_qty"] == 2
    assert logged_in.get(f"/api/v1/purchases/{pid}").json() == done.json()

    refused = logged_in.post(
        f"/api/v1/purchases/{pid}/status", json={"status": "cancelled"}, headers=headers
    )
    assert refused.status_code == 409
    wrong_purchase = logged_in.post(
        f"/api/v1/purchases/{canonical.clamp.id}/lines/{clamp_line}/receive",
        json={"qty": 1},
        headers=headers,
    )
    assert wrong_purchase.status_code in (404, 409)
    assert logged_in.get("/api/v1/purchases/nope").status_code == 404


def test_catalog_merge_over_the_api(logged_in: TestClient, canonical: CanonicalWorkspace) -> None:
    headers = _csrf(logged_in)
    dupe = logged_in.post(
        "/api/v1/catalog/items",
        json={"name": "Bar clamp (dupe)", "kind": "reusable"},
        headers=headers,
    ).json()
    merged = logged_in.post(
        f"/api/v1/catalog/items/{dupe['id']}/merge",
        json={"target_id": canonical.clamp.id},
        headers=headers,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["id"] == canonical.clamp.id
    redirect = logged_in.get(f"/api/v1/catalog/items/{dupe['id']}").json()
    assert redirect["merged_into_id"] == canonical.clamp.id
    listed = {i["id"] for i in logged_in.get("/api/v1/catalog/items").json()}
    assert dupe["id"] not in listed
    everything = {
        i["id"] for i in logged_in.get("/api/v1/catalog/items?include_merged=true").json()
    }
    assert dupe["id"] in everything
    mismatch = logged_in.post(
        f"/api/v1/catalog/items/{canonical.bolts.id}/merge",
        json={"target_id": canonical.clamp.id},
        headers=headers,
    )
    assert mismatch.status_code == 409
    assert "Kinds differ" in mismatch.json()["detail"]


def test_export_and_import_over_the_api(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    headers = _csrf(logged_in)
    exported = logged_in.get("/api/v1/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].startswith("attachment")
    document = exported.json()
    assert document["format"] == "projectbudget-workspace"
    assert document["currency"] == "USD"
    assert len(document["projects"]) == 2
    backlog_before = logged_in.get("/api/v1/backlog").json()

    refused = logged_in.post("/api/v1/import", json=document, headers=headers)
    assert refused.status_code == 409
    replaced = logged_in.post("/api/v1/import?replace=true", json=document, headers=headers)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["replaced"] is True
    assert replaced.json()["projects"] == 2
    assert logged_in.get("/api/v1/backlog").json() == backlog_before

    bad = logged_in.post("/api/v1/import?replace=true", json={"format": "nope"}, headers=headers)
    assert bad.status_code == 422
    assert "Not a ProjectBudget export" in bad.json()["detail"]
