"""The HTML journey: create a project, add requirements, price items, consume, reorder (ADR-002)."""

from fastapi.testclient import TestClient

from conftest import csrf_token_from


def _csrf(client: TestClient) -> str:
    return csrf_token_from(client.get("/projects").text)


def _location_id(response: object) -> str:
    location: str = response.headers["location"]  # type: ignore[attr-defined]
    return location.rsplit("/", 1)[1]


def test_home_is_the_backlog(logged_in: TestClient) -> None:
    home = logged_in.get("/")
    assert home.status_code == 200
    assert "<h1>Backlog</h1>" in home.text
    # A fresh workspace has no requirements: say so and point at Projects (#20).
    assert "Nothing here yet: no project has requirements" in home.text
    assert "Nothing to buy" not in home.text
    assert 'id="backlog-empty"' in home.text
    assert '<a href="/projects">Create a project and add what it needs</a>' in home.text
    assert 'href="/catalog"' in home.text


def test_backlog_says_covered_by_stock_only_once_requirements_exist(
    logged_in: TestClient,
) -> None:
    csrf = csrf_token_from(logged_in.get("/projects").text)
    project = logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    project_id = project.headers["location"].rsplit("/", 1)[1]
    logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "item_text": "Hand saw", "qty": "1"},
    )
    unpriced = logged_in.get("/backlog").text
    # The only requirement is unpriced: the main table says why it is empty, not "covered".
    assert "Nothing priced to buy yet: the 1 item below needs a price first." in unpriced
    assert "covered by stock" not in unpriced
    item_id = unpriced.split('href="/catalog/')[1].split('"')[0]
    stocked = logged_in.post(
        f"/catalog/{item_id}/adjustments",
        data={"csrf_token": csrf, "delta": "1", "occurred_on": "2026-11-08", "note": "found one"},
    )
    assert stocked.status_code in (200, 303), stocked.text
    covered = logged_in.get("/backlog").text
    assert "Nothing to buy: every active requirement is covered by stock." in covered
    assert "Nothing here yet" not in covered

    # Pause the only requirement: nothing is active, which is not the same as nothing at all.
    requirement_id = (
        logged_in.get(f"/projects/{project_id}").text.split("/requirements/")[1].split("/")[0]
    )
    paused = logged_in.post(
        f"/requirements/{requirement_id}/intent", data={"csrf_token": csrf, "intent": "paused"}
    )
    assert paused.status_code in (200, 303), paused.text
    idle = logged_in.get("/backlog").text
    assert "No active requirements: everything is paused, cancelled or archived." in idle
    assert "Nothing here yet" not in idle
    assert "covered by stock" not in idle


def test_core_journey_through_the_pages(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)

    created = logged_in.post(
        "/projects", data={"csrf_token": csrf, "name": "Workbench", "notes": ""}
    )
    assert created.status_code == 303
    project_id = _location_id(created)
    page = logged_in.get(f"/projects/{project_id}")
    assert page.status_code == 200
    assert "Workbench" in page.text
    assert "Nothing needed yet" in page.text

    # Type a new item: it becomes an unreviewed catalog item.
    added = logged_in.post(
        f"/projects/{project_id}/requirements",
        data={
            "csrf_token": csrf,
            "item_text": "Bar clamp",
            "qty": "3",
            "priority": "2",
            "sharing": "shared",
            "unit": "",
        },
    )
    assert added.status_code == 303
    detail = logged_in.get(f"/projects/{project_id}").text
    assert "Bar clamp" in detail
    assert "Pause" in detail
    assert "Cancel" in detail

    catalog_page = logged_in.get("/catalog").text
    assert "bar-clamp" in catalog_page
    assert "unreviewed" in catalog_page
    assert "no price" in catalog_page
    item_id = catalog_page.split('href="/catalog/')[1].split('"')[0]

    # Make it a reusable tool, price it, and count one in stock.
    saved = logged_in.post(
        f"/catalog/{item_id}",
        data={
            "csrf_token": csrf,
            "name": "Bar clamp 600 mm",
            "kind": "reusable",
            "unit": "each",
            "review_state": "confirmed",
        },
    )
    assert saved.status_code == 303
    priced = logged_in.post(
        f"/catalog/{item_id}/estimates",
        data={"csrf_token": csrf, "price": "12.50", "as_of": "2026-11-01", "confidence": "high"},
    )
    assert priced.status_code == 303
    counted = logged_in.post(
        f"/catalog/{item_id}/adjustments",
        data={"csrf_token": csrf, "delta": "1", "note": "stock take"},
    )
    assert counted.status_code == 303
    item_page = logged_in.get(f"/catalog/{item_id}").text
    assert "12.50 USD" in item_page
    assert ">1 each<" in item_page
    assert "stock take" in item_page

    backlog = logged_in.get("/backlog").text
    assert "Bar clamp 600 mm" in backlog
    assert "<strong>2</strong>" in backlog  # needs 3, owns 1
    assert "25.00 USD" in backlog
    assert "shared" in backlog


def test_exclusive_and_consumable_sharing_are_both_shown(logged_in: TestClient) -> None:
    """#19: exclusive is a deliberate choice, not "unspecified"; consumables show it greyed out."""
    csrf = _csrf(logged_in)
    project_id = _location_id(
        logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    )
    vise = logged_in.post(
        "/catalog",
        data={"csrf_token": csrf, "name": "Bench vise", "kind": "reusable", "unit": "each"},
    )
    vise_id = _location_id(vise)
    for item, sharing in ((vise_id, "exclusive"), ("", "shared")):
        added = logged_in.post(
            f"/projects/{project_id}/requirements",
            data={
                "csrf_token": csrf,
                "catalog_item_id": item,
                "item_text": "" if item else "Wood glue",
                "qty": "1",
                "sharing": sharing,
            },
        )
        assert added.status_code == 303, added.text

    detail = logged_in.get(f"/projects/{project_id}").text
    assert 'data-label="Sharing">exclusive</td>' in detail
    assert "—" not in detail.split('id="requirements"')[1]
    assert (
        'title="Sharing only matters for reusable items; a consumable is used up">shared</span>'
        in detail
    )
    assert 'id="sharing-note"' in detail
    assert "manual</span>" not in detail  # not a synced project: no manual badge

    # Priced, so the vise sits in the main table (the unpriced bucket shows no sharing chips).
    priced = logged_in.post(
        f"/catalog/{vise_id}/estimates",
        data={"csrf_token": csrf, "price": "99.00", "as_of": "2026-11-01", "confidence": "high"},
    )
    assert priced.status_code == 303, priced.text
    backlog = logged_in.get("/backlog").text
    vise_row = backlog.split(f'href="/catalog/{vise_id}"')[1].split("</tr>")[0]
    assert ">exclusive</span>" in vise_row
    assert ">shared</span>" not in vise_row


def test_form_errors_re_render_the_page_with_422(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    blank = logged_in.post("/projects", data={"csrf_token": csrf, "name": "   "})
    assert blank.status_code == 422
    assert "Name must be" in blank.text

    created = logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    project_id = _location_id(created)
    nothing = logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "qty": "1", "item_text": ""},
    )
    assert nothing.status_code == 422
    assert "catalog_item_id or item_text" in nothing.text

    logged_in.post(
        "/catalog", data={"csrf_token": csrf, "name": "Wire", "kind": "consumable", "unit": "m"}
    )
    mismatch = logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "qty": "5", "item_text": "Wire", "unit": "ft"},
    )
    assert mismatch.status_code == 422
    assert "does not match" in mismatch.text

    dup = logged_in.post(
        "/catalog", data={"csrf_token": csrf, "name": "Wire", "kind": "consumable", "unit": "m"}
    )
    assert dup.status_code == 422
    assert "already used" in dup.text

    assert logged_in.get("/projects/nope").status_code == 404
    assert logged_in.get("/catalog/nope").status_code == 404


def test_requirement_actions_swap_rows_over_htmx(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    project_id = _location_id(
        logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    )
    logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "qty": "4", "item_text": "Hinge"},
    )
    requirement_id = logged_in.get(f"/api/v1/projects/{project_id}/requirements").json()[0]["id"]
    item_id = logged_in.get(f"/api/v1/requirements/{requirement_id}").json()["catalog_item_id"]
    hx = {"HX-Request": "true"}

    paused = logged_in.post(
        f"/requirements/{requirement_id}/intent",
        data={"csrf_token": csrf, "intent": "paused"},
        headers=hx,
    )
    assert paused.status_code == 200
    assert paused.text.lstrip().startswith("<tr")
    assert "paused" in paused.text
    assert "Reactivate" in paused.text
    assert "<html" not in paused.text

    plain = logged_in.post(
        f"/requirements/{requirement_id}/intent", data={"csrf_token": csrf, "intent": "active"}
    )
    assert plain.status_code == 303
    assert plain.headers["location"] == f"/projects/{project_id}"

    # Consume: refused without stock (the row re-renders with the error), accepted with stock.
    refused = logged_in.post(
        f"/requirements/{requirement_id}/consume", data={"csrf_token": csrf, "qty": "1"}, headers=hx
    )
    assert refused.status_code == 422
    assert "Only 0 on hand" in refused.text
    logged_in.post(f"/catalog/{item_id}/adjustments", data={"csrf_token": csrf, "delta": "2"})
    used = logged_in.post(
        f"/requirements/{requirement_id}/consume", data={"csrf_token": csrf, "qty": "1"}, headers=hx
    )
    assert used.status_code == 200
    assert '<td class="num" data-label="Consumed">1</td>' in used.text


def test_backlog_reorder_over_htmx_and_plain_post(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    project_id = _location_id(
        logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    )
    ids = []
    for name, price in (("Alpha", "1.00"), ("Beta", "2.00")):
        logged_in.post(
            f"/projects/{project_id}/requirements",
            data={"csrf_token": csrf, "qty": "1", "item_text": name},
        )
        item_id = logged_in.get("/api/v1/catalog/items").json()[-1]["id"]
        logged_in.post(f"/catalog/{item_id}/estimates", data={"csrf_token": csrf, "price": price})
        ids.append(item_id)
    assert [r["item_id"] for r in logged_in.get("/api/v1/backlog").json()["rows"]] == ids

    moved = logged_in.post(
        f"/backlog/{ids[1]}/move",
        data={"csrf_token": csrf, "direction": "up"},
        headers={"HX-Request": "true"},
    )
    assert moved.status_code == 200
    assert "<html" not in moved.text
    assert moved.text.index(f'id="row-{ids[1]}"') < moved.text.index(f'id="row-{ids[0]}"')

    back = logged_in.post(f"/backlog/{ids[1]}/move", data={"csrf_token": csrf, "direction": "down"})
    assert back.status_code == 303
    assert [r["item_id"] for r in logged_in.get("/api/v1/backlog").json()["rows"]] == ids
    sideways = logged_in.post(
        f"/backlog/{ids[0]}/move", data={"csrf_token": csrf, "direction": "left"}
    )
    assert sideways.status_code == 422
