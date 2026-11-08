"""The sync pages: add a source, preview a plan (fetched or uploaded), apply, runs, detach."""

from fastapi.testclient import TestClient

from conftest import csrf_token_from
from projectbudget.settings import Settings

MANIFEST = """version: 1
project: {id: shed, name: Garden shed}
items:
  - {id: screws, name: Deck screws, qty: 100, estimate: {amount: "9.50", currency: USD}}
  - {id: saw, name: Hand saw, qty: 1, kind: reusable}
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
    assert len(content_hash) == 64

    applied = logged_in.post(
        f"/sync/{source_id}/apply",
        data={"csrf_token": csrf, "expected_content_hash": content_hash, "force": "true"},
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
