"""The sync pages: add a source, preview a plan (fetched or uploaded), apply, runs, detach."""

from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget import localtime
from projectbudget.settings import Settings

MANIFEST = """version: 1
project: {id: shed, name: Garden shed}
items:
  - {id: screws, name: Deck screws, qty: 100, estimate: {amount: "9.50", currency: USD}}
  - {id: saw, name: Hand saw, qty: 1, kind: reusable}
"""
SWAPPED = """version: 1
project: {id: shed, name: Garden shed}
items:
  - {id: saw, name: Hand saw, qty: 1, kind: reusable}
  - {id: screws, name: Deck screws, qty: 100, estimate: {amount: "9.50", currency: USD}}
"""


def _csrf(client: TestClient) -> str:
    return csrf_token_from(client.get("/sync").text)


def _import_dir(client: TestClient):  # type: ignore[no-untyped-def]
    settings: Settings = client.app.state.settings  # type: ignore[attr-defined]
    path = settings.effective_sync_import_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_sync_pages_end_to_end(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    (_import_dir(logged_in) / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    page = logged_in.get("/sync")
    assert page.status_code == 200
    assert "No sources yet" in page.text

    created = logged_in.post(
        "/sync", data={"csrf_token": csrf, "kind": "file", "locator": "shed.yaml"}
    )
    assert created.status_code == 303
    source_id = created.headers["location"].rsplit("/", 1)[1]
    detail = logged_in.get(f"/sync/{source_id}")
    assert detail.status_code == 200
    assert "not synced yet" in logged_in.get("/sync").text
    assert "No runs yet" in detail.text

    shown = logged_in.post(f"/sync/{source_id}/preview", data={"csrf_token": csrf})
    assert shown.status_code == 200
    assert 'id="plan"' in shown.text
    assert "Deck screws" in shown.text
    assert "new unreviewed item" in shown.text
    assert "Apply this plan" in shown.text
    content_hash = shown.text.split('name="expected_content_hash" value="')[1].split('"')[0]
    plan_digest = shown.text.split('name="expected_plan_digest" value="')[1].split('"')[0]
    assert len(content_hash) == len(plan_digest) == 64

    applied = logged_in.post(
        f"/sync/{source_id}/apply",
        data={
            "csrf_token": csrf,
            "expected_content_hash": content_hash,
            "expected_plan_digest": plan_digest,
            "force": "true",
        },
    )
    assert applied.status_code == 200
    assert "Applied" in applied.text
    assert "2 created" in applied.text
    assert "Garden shed" in logged_in.get("/sync").text
    assert "<strong>100</strong>" in logged_in.get("/backlog").text  # screws: needs 100, none owned

    again = logged_in.post(f"/sync/{source_id}/preview", data={"csrf_token": csrf})
    assert "nothing to do" in again.text.lower()

    uploaded = logged_in.post(
        f"/sync/{source_id}/preview",
        data={"csrf_token": csrf},
        files={
            "file": ("shed.yaml", MANIFEST.replace("qty: 100", "qty: 120").encode(), "text/yaml")
        },
    )
    assert uploaded.status_code == 200
    assert "qty 100 → 120" in uploaded.text

    # Items swapped in the file: the plan shows the move as 1-based positions (#34).
    swapped = logged_in.post(
        f"/sync/{source_id}/preview",
        data={"csrf_token": csrf},
        files={"file": ("shed.yaml", SWAPPED.encode(), "text/yaml")},
    )
    assert swapped.status_code == 200
    assert "position 2 → 1" in swapped.text
    assert "position 1 → 2" in swapped.text
    assert "sort_order" not in swapped.text
    assert 'name="content_b64"' in uploaded.text
    content_b64 = uploaded.text.split('name="content_b64" value="')[1].split('"')[0]
    uploaded_hash = uploaded.text.split('name="expected_content_hash" value="')[1].split('"')[0]
    applied_upload = logged_in.post(
        f"/sync/{source_id}/apply",
        data={
            "csrf_token": csrf,
            "expected_content_hash": uploaded_hash,
            "content_b64": content_b64,
            "force": "true",
        },
    )
    assert applied_upload.status_code == 200
    assert "1 updated" in applied_upload.text
    assert "<strong>120</strong>" in logged_in.get("/backlog").text

    rejected = logged_in.post(
        f"/sync/{source_id}/preview",
        data={"csrf_token": csrf},
        files={
            "file": (
                "bad.yaml",
                b"version: 1\nproject: {id: shed}\nitems:\n  - {id: a, name: A, qty: 1, estimate: {amount: 1.5}}\n",
                "text/yaml",
            )
        },
    )
    assert rejected.status_code == 422
    assert "quoted decimal string" in rejected.text
    runs_page = logged_in.get(f"/sync/{source_id}").text
    assert "rejected" in runs_page
    assert "applied" in runs_page

    detached = logged_in.post(f"/sync/{source_id}/detach", data={"csrf_token": csrf})
    assert detached.status_code == 303
    assert "No sources yet" in logged_in.get("/sync").text
    assert "Garden shed" in logged_in.get("/projects").text
    assert logged_in.get(f"/sync/{source_id}").status_code == 404


def test_add_source_into_an_existing_project_shows_adoptions(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    (_import_dir(logged_in) / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    project = logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed by hand"})
    project_id = project.headers["location"].rsplit("/", 1)[1]
    added = logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "item_text": "Deck screws", "qty": "1", "priority": "3"},
    )
    assert added.status_code in (200, 303), added.text

    form = logged_in.get("/sync").text
    assert '<select id="project_id" name="project_id">' in form
    assert f'<option value="{project_id}">Shed by hand</option>' in form

    created = logged_in.post(
        "/sync",
        data={"csrf_token": csrf, "kind": "file", "locator": "shed.yaml", "project_id": project_id},
    )
    assert created.status_code == 303
    source_id = created.headers["location"].rsplit("/", 1)[1]
    assert "Shed by hand" in logged_in.get(f"/sync/{source_id}").text
    # The project is taken: it no longer appears as a choice for another source.
    assert f'<option value="{project_id}">' not in logged_in.get("/sync").text

    shown = logged_in.post(f"/sync/{source_id}/preview", data={"csrf_token": csrf})
    assert shown.status_code == 200
    assert ">adopt</span>" in shown.text
    assert "Deck screws: your requirement becomes this source" in shown.text
    assert "qty 1 → 100; priority 3 → 2" in shown.text
    assert "Hand saw" in shown.text  # created beside it; a hand-entered saw would be consumable
    content_hash = shown.text.split('name="expected_content_hash" value="')[1].split('"')[0]
    plan_digest = shown.text.split('name="expected_plan_digest" value="')[1].split('"')[0]
    applied = logged_in.post(
        f"/sync/{source_id}/apply",
        data={
            "csrf_token": csrf,
            "expected_content_hash": content_hash,
            "expected_plan_digest": plan_digest,
            "force": "true",
        },
    )
    assert applied.status_code == 200
    assert "1 created, 1 adopted" in applied.text
    projects_page = logged_in.get("/projects").text
    assert projects_page.count("Garden shed") >= 1
    assert "Shed by hand" not in projects_page  # renamed by the manifest, not duplicated


def test_github_source_page_says_which_token_it_uses_and_lets_you_change_settings(
    logged_in: TestClient,
) -> None:
    """#31: the placeholder cannot pass for a value; #29: settings are editable in place."""
    csrf = _csrf(logged_in)
    form = logged_in.get("/sync").text
    assert 'placeholder="GITHUB_TOKEN"' not in form
    assert 'placeholder="name of a variable, e.g. MY_GITHUB_TOKEN"' in form
    assert "PROJECTBUDGET_GITHUB_TOKEN" in form

    created = logged_in.post(
        "/sync", data={"csrf_token": csrf, "kind": "github", "locator": "someone/repo"}
    )
    assert created.status_code == 303
    source_id = created.headers["location"].rsplit("/", 1)[1]
    page = logged_in.get(f"/sync/{source_id}").text
    assert 'id="token-state"' in page
    assert "<strong>none</strong>" in page  # the test server has no default token
    assert 'id="source-settings"' in page

    saved = logged_in.post(
        f"/sync/{source_id}/settings",
        data={"csrf_token": csrf, "branch": "main", "path": "", "token_env": "MY_GITHUB_TOKEN"},
    )
    assert saved.status_code == 303
    page = logged_in.get(f"/sync/{source_id}").text
    assert "environment variable <code>MY_GITHUB_TOKEN</code>" in page
    assert 'value="main"' in page

    refused = logged_in.post(
        f"/sync/{source_id}/settings",
        data={"csrf_token": csrf, "branch": "main", "token_env": "ghp_pasted"},
    )
    assert refused.status_code == 422
    assert "NAME" in refused.text
    assert logged_in.get("/sync/nope/settings").status_code in (404, 405)


def test_a_reject_row_links_the_item_and_says_why(logged_in: TestClient) -> None:
    """#28: the owner's case, a tool entered by hand that the manifest calls reusable."""
    csrf = _csrf(logged_in)
    (_import_dir(logged_in) / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    project = logged_in.post("/projects", data={"csrf_token": csrf, "name": "Shed"})
    project_id = project.headers["location"].rsplit("/", 1)[1]
    logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "item_text": "Hand saw", "qty": "1"},
    )
    created = logged_in.post(
        "/sync",
        data={"csrf_token": csrf, "kind": "file", "locator": "shed.yaml", "project_id": project_id},
    )
    source_id = created.headers["location"].rsplit("/", 1)[1]
    shown = logged_in.post(f"/sync/{source_id}/preview", data={"csrf_token": csrf}).text
    row = shown.split('<span class="badge warn">reject</span>')[1].split("</tr>")[0]
    assert 'href="/catalog/' in row
    assert (
        ">Hand saw</a>: kind &#39;reusable&#39; in the manifest, &#39;consumable&#39; in the catalog"
        in row
    )
    assert (
        "because this source did not create it and it is used by a requirement you entered by hand"
        in row
    )
    assert "Either change the item&#39;s kind to &#39;reusable&#39; on its page" in row
    assert 'id="reject-summary"' not in shown  # one reject: nothing to summarise
    assert "external_id unset → shed" in shown  # a hand-built project had no external id yet


def test_add_source_validation_renders_the_error(logged_in: TestClient) -> None:
    csrf = _csrf(logged_in)
    bad = logged_in.post(
        "/sync", data={"csrf_token": csrf, "kind": "github", "locator": "not-a-repo"}
    )
    assert bad.status_code == 422
    assert "owner/repo" in bad.text
    csv_without_project = logged_in.post(
        "/sync", data={"csrf_token": csrf, "kind": "csv", "locator": "x.csv"}
    )
    assert csv_without_project.status_code == 422
    assert "external project id" in csv_without_project.text


def _apply_file_source(client: TestClient, manifest: str, name: str = "shed.yaml") -> str:
    """Create a file source, preview and apply it; return the source id."""
    csrf = _csrf(client)
    (_import_dir(client) / name).write_text(manifest, encoding="utf-8")
    created = client.post("/sync", data={"csrf_token": csrf, "kind": "file", "locator": name})
    source_id = created.headers["location"].rsplit("/", 1)[1]
    shown = client.post(f"/sync/{source_id}/preview", data={"csrf_token": csrf}).text
    applied = client.post(
        f"/sync/{source_id}/apply",
        data={
            "csrf_token": csrf,
            "expected_content_hash": shown.split('name="expected_content_hash" value="')[1].split(
                '"'
            )[0],
            "expected_plan_digest": shown.split('name="expected_plan_digest" value="')[1].split(
                '"'
            )[0],
        },
    )
    assert applied.status_code == 200, applied.text
    return source_id


def test_synced_project_page_names_its_source_and_marks_manual_rows(
    logged_in: TestClient,
) -> None:
    """#35: the project says where it comes from, when it was applied, and what stays manual."""
    csrf = _csrf(logged_in)
    source_id = _apply_file_source(logged_in, MANIFEST)
    sync_page = logged_in.get("/sync").text
    project_id = sync_page.split('href="/projects/')[1].split('"')[0]

    page = logged_in.get(f"/projects/{project_id}").text
    assert 'id="synced-from"' in page
    assert f'<a href="/sync/{source_id}">shed.yaml</a>' in page
    assert "last applied 20" in page  # a date, then the time
    assert f"({localtime.label()})" in page  # in the workspace time zone, named
    assert "not applied yet" not in page
    assert 'id="manual-note"' in page
    assert "<strong>manual</strong>" in page
    assert "manual</span>" not in page  # every row so far comes from the manifest

    # A hand-added row on a synced project is badged, the manifest's rows are not.
    added = logged_in.post(
        f"/projects/{project_id}/requirements",
        data={"csrf_token": csrf, "item_text": "Tape measure", "qty": "1"},
    )
    assert added.status_code == 303, added.text
    page = logged_in.get(f"/projects/{project_id}").text
    rows = page.split('id="requirements"')[1].split("<tr id=")[1:]
    badged = [row for row in rows if "manual</span>" in row]
    assert len(badged) == 1
    assert "Tape measure" in badged[0]
    # The HTMX row swap renders the same badge.
    requirement_id = badged[0].split('"')[1].removeprefix("requirement-")
    swapped = logged_in.post(
        f"/requirements/{requirement_id}/intent",
        data={"csrf_token": csrf, "intent": "paused"},
        headers={"HX-Request": "true"},
    )
    assert swapped.status_code == 200
    assert "manual</span>" in swapped.text

    # A project built by hand says none of this.
    manual_project = logged_in.post("/projects", data={"csrf_token": csrf, "name": "By hand"})
    manual_page = logged_in.get(manual_project.headers["location"]).text
    assert 'id="synced-from"' not in manual_page
    assert 'id="manual-note"' not in manual_page
