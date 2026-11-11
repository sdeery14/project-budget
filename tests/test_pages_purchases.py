"""The purchases pages, the merge form on an item page, and the export/import page."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.persistence.session import SessionFactory

AS_OF = date(2026, 11, 1)


def _csrf(client: TestClient) -> str:
    return csrf_token_from(client.get("/purchases").text)


@pytest.fixture
def canonical(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=AS_OF)


def test_purchase_pages_create_receive_partially_and_close(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    csrf = _csrf(logged_in)
    empty = logged_in.get("/purchases")
    assert empty.status_code == 200
    assert "No purchases yet" in empty.text

    created = logged_in.post(
        "/purchases",
        data={
            "csrf_token": csrf,
            "catalog_item_id": canonical.clamp.id,
            "qty": "2",
            "unit_price": "12.50",
            "vendor": "Tool Shop",
            "status": "ordered",
            "shipping": "5.00",
            "tax": "0",
        },
    )
    assert created.status_code == 303
    purchase_id = created.headers["location"].rsplit("/", 1)[1]
    page = logged_in.get(f"/purchases/{purchase_id}").text
    assert "Tool Shop" in page
    assert 'id="purchase-total">30.00 USD' in page
    assert 'id="outstanding">2<' in page
    assert "Receive everything" in page

    line_id = logged_in.get(f"/api/v1/purchases/{purchase_id}").json()["lines"][0]["id"]
    partial = logged_in.post(
        f"/purchases/{purchase_id}/lines/{line_id}/receive", data={"csrf_token": csrf, "qty": "1"}
    )
    assert partial.status_code == 303
    page = logged_in.get(f"/purchases/{purchase_id}").text
    assert "1 / 2" in page
    assert 'id="outstanding">1<' in page
    backlog = logged_in.get("/backlog").text
    clamp_row = backlog[backlog.index(f'id="row-{canonical.clamp.id}"') :]
    clamp_row = clamp_row[: clamp_row.index("</tr>")]
    assert "<strong>1</strong>" in clamp_row  # needs 3, owns 2 now

    too_many = logged_in.post(
        f"/purchases/{purchase_id}/lines/{line_id}/receive", data={"csrf_token": csrf, "qty": "5"}
    )
    assert too_many.status_code == 422
    assert "still to receive" in too_many.text

    added = logged_in.post(
        f"/purchases/{purchase_id}/lines",
        data={
            "csrf_token": csrf,
            "catalog_item_id": canonical.bolts.id,
            "qty": "4",
            "unit_price": "0.45",
        },
    )
    assert added.status_code == 303
    closed = logged_in.post(
        f"/purchases/{purchase_id}/status", data={"csrf_token": csrf, "status": "received"}
    )
    assert closed.status_code == 303
    page = logged_in.get(f"/purchases/{purchase_id}").text
    assert "Received in full" in page
    listed = logged_in.get("/purchases").text
    assert f'id="purchase-{purchase_id}"' in listed
    assert logged_in.get("/purchases/nope").status_code == 404


def test_merge_form_on_the_item_page(logged_in: TestClient, canonical: CanonicalWorkspace) -> None:
    csrf = _csrf(logged_in)
    dupe = logged_in.post(
        "/catalog",
        data={"csrf_token": csrf, "name": "Bar clamp (dupe)", "kind": "reusable", "unit": "each"},
    )
    dupe_id = dupe.headers["location"].rsplit("/", 1)[1]
    page = logged_in.get(f"/catalog/{dupe_id}").text
    assert "Merge into another item" in page
    assert canonical.clamp.id in page  # offered as a target: same unit and kind
    # Nothing is preselected and the form asks first, naming both items (#17).
    assert '<option value="" selected>choose the item to keep' in page
    assert 'data-confirm="Merge “Bar clamp (dupe)” into “{target}”?' in page
    assert "confirm.js" in page
    blank = logged_in.post(f"/catalog/{dupe_id}/merge", data={"csrf_token": csrf, "target_id": ""})
    assert blank.status_code == 422
    assert "Choose the item to keep" in blank.text
    assert (
        canonical.bolts.id not in page.split("Merge into another item")[1]
    )  # consumable: not offered

    merged = logged_in.post(
        f"/catalog/{dupe_id}/merge", data={"csrf_token": csrf, "target_id": canonical.clamp.id}
    )
    assert merged.status_code == 303
    assert merged.headers["location"] == f"/catalog/{canonical.clamp.id}"
    redirect_page = logged_in.get(f"/catalog/{dupe_id}").text
    assert "was merged into" in redirect_page
    assert "Merge into another item" not in redirect_page
    assert "Bar clamp (dupe)" not in logged_in.get("/catalog").text

    refused = logged_in.post(
        f"/catalog/{canonical.bolts.id}/merge",
        data={"csrf_token": csrf, "target_id": canonical.clamp.id},
    )
    assert refused.status_code == 422
    assert "Kinds differ" in refused.text


def test_data_page_exports_and_imports(
    logged_in: TestClient, canonical: CanonicalWorkspace
) -> None:
    csrf = _csrf(logged_in)
    page = logged_in.get("/settings/data")
    assert page.status_code == 200
    assert "/settings/data/export.json" in page.text

    download = logged_in.get("/settings/data/export.json")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")
    document = download.json()
    assert len(document["catalog_items"]) == 4

    refused = logged_in.post(
        "/settings/data/import",
        data={"csrf_token": csrf},
        files={"file": ("export.json", json.dumps(document).encode(), "application/json")},
    )
    assert refused.status_code == 422
    assert "already has data" in refused.text

    replaced = logged_in.post(
        "/settings/data/import",
        data={"csrf_token": csrf, "replace": "true"},
        files={"file": ("export.json", json.dumps(document).encode(), "application/json")},
    )
    assert replaced.status_code == 200
    assert "Import complete" in replaced.text
    assert "previous data replaced" in replaced.text
    assert "2 projects" in replaced.text

    garbage = logged_in.post(
        "/settings/data/import",
        data={"csrf_token": csrf, "replace": "true"},
        files={"file": ("x.json", b"{not json", "application/json")},
    )
    assert garbage.status_code == 422
    assert "Not JSON" in garbage.text
