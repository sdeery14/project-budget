"""JSON twins for sync: sources, preview, apply, runs, detach; manifests sent inline."""

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import csrf_token_from, make_client
from projectbudget.settings import Settings

MANIFEST = """version: 1
project: {id: shed, name: Garden shed}
items:
  - {id: screws, name: Deck screws, qty: 100, estimate: {amount: "9.50", currency: USD}}
  - {id: saw, name: Hand saw, qty: 1, kind: reusable}
"""


def _csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token_from(client.get("/projects").text)}


def _settings(client: TestClient) -> Settings:
    settings: Settings = client.app.state.settings  # type: ignore[attr-defined]
    return settings


def test_a_source_can_be_attached_to_a_project_and_the_project_says_so(
    logged_in: TestClient,
) -> None:
    headers = _csrf(logged_in)
    import_dir = _settings(logged_in).effective_sync_import_dir
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    project = logged_in.post("/api/v1/projects", json={"name": "Shed by hand"}, headers=headers)
    assert project.status_code == 201, project.text
    assert project.json()["source_id"] is None
    project_id = project.json()["id"]

    created = logged_in.post(
        "/api/v1/sync/sources",
        json={"kind": "file", "locator": "shed.yaml", "project_id": project_id},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert (source["project_id"], source["project_name"]) == (project_id, "Shed by hand")
    assert logged_in.get(f"/api/v1/projects/{project_id}").json()["source_id"] == source["id"]
    assert logged_in.get("/api/v1/projects").json()[0]["source_id"] == source["id"]

    again = logged_in.post(
        "/api/v1/sync/sources",
        json={"kind": "file", "locator": "shed.yaml", "project_id": project_id},
        headers=headers,
    )
    assert again.status_code == 422
    assert "already synced" in again.text


def test_source_settings_patch_keeps_absent_fields_and_clears_empty_ones(
    logged_in: TestClient,
) -> None:
    headers = _csrf(logged_in)
    created = logged_in.post(
        "/api/v1/sync/sources",
        json={"kind": "github", "locator": "o/r", "branch": "main", "token_env": "PB_TOKEN"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    patched = logged_in.patch(
        f"/api/v1/sync/sources/{source_id}",
        json={"path": "deep/project-budget.yaml"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["branch"], patched.json()["path"], patched.json()["token_env"]) == (
        "main",
        "deep/project-budget.yaml",
        "PB_TOKEN",
    )
    cleared = logged_in.patch(
        f"/api/v1/sync/sources/{source_id}", json={"token_env": "", "branch": ""}, headers=headers
    )
    assert (cleared.json()["branch"], cleared.json()["token_env"]) == (None, None)
    assert cleared.json()["path"] == "deep/project-budget.yaml"

    refused = logged_in.patch(
        f"/api/v1/sync/sources/{source_id}", json={"token_env": "ghp_not_a_name"}, headers=headers
    )
    assert refused.status_code == 422
    assert "NAME" in refused.text
    assert logged_in.patch("/api/v1/sync/sources/nope", json={}, headers=headers).status_code == 404


def test_sources_preview_apply_runs_and_detach(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    import_dir = _settings(logged_in).effective_sync_import_dir
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / "shed.yaml").write_text(MANIFEST, encoding="utf-8")

    created = logged_in.post(
        "/api/v1/sync/sources", json={"kind": "file", "locator": "shed.yaml"}, headers=headers
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["project_id"] is None
    assert logged_in.get("/api/v1/sync/sources").json() == [source]
    assert logged_in.get(f"/api/v1/sync/sources/{source['id']}").json() == source

    shown = logged_in.post(f"/api/v1/sync/sources/{source['id']}/preview", json={}, headers=headers)
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["noop"] is False
    assert body["description"] == "file shed.yaml"
    assert body["plan"]["counts"]["creates"] == 2
    assert body["plan"]["creates"][0]["estimate"] == {
        "amount": "9.50",
        "minor": 950,
        "currency": "USD",
    }
    assert (
        logged_in.get(f"/api/v1/sync/sources/{source['id']}/runs").json() == []
    )  # a preview records nothing

    applied = logged_in.post(
        f"/api/v1/sync/sources/{source['id']}/apply",
        json={
            "expected_content_hash": body["content_hash"],
            "expected_plan_digest": body["plan"]["digest"],
        },
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["run"]["status"] == "applied"
    assert applied.json()["source"]["project_name"] == "Garden shed"
    project_id = applied.json()["source"]["project_id"]
    assert len(logged_in.get(f"/api/v1/projects/{project_id}/requirements").json()) == 2

    again = logged_in.post(f"/api/v1/sync/sources/{source['id']}/apply", json={}, headers=headers)
    assert again.json()["noop"] is True
    runs = logged_in.get(f"/api/v1/sync/sources/{source['id']}/runs").json()
    assert [r["status"] for r in runs] == ["noop", "applied"]

    inline = logged_in.post(
        f"/api/v1/sync/sources/{source['id']}/preview",
        json={"manifest_text": MANIFEST.replace("qty: 100", "qty: 120")},
        headers=headers,
    )
    assert inline.json()["plan"]["updates"][0]["changes"] == [
        {"field": "qty", "old": 100, "new": 120}
    ]

    stale = logged_in.post(
        f"/api/v1/sync/sources/{source['id']}/apply",
        json={
            "manifest_text": MANIFEST.replace("qty: 100", "qty: 120"),
            "expected_content_hash": "0" * 64,
        },
        headers=headers,
    )
    assert stale.status_code == 409
    drifted = logged_in.post(
        f"/api/v1/sync/sources/{source['id']}/apply",
        json={
            "manifest_text": MANIFEST.replace("qty: 100", "qty: 120"),
            "expected_plan_digest": "0" * 64,
        },
        headers=headers,
    )
    assert drifted.status_code == 409

    rejected = logged_in.post(
        f"/api/v1/sync/sources/{source['id']}/preview",
        json={
            "manifest_text": "version: 1\nproject: {id: shed}\nitems:\n  - {id: a, name: A, qty: 1, estimate: {amount: 1.5}}\n"
        },
        headers=headers,
    )
    assert rejected.status_code == 422
    assert "quoted decimal string" in rejected.json()["detail"]

    gone = logged_in.delete(f"/api/v1/sync/sources/{source['id']}", headers=headers)
    assert gone.status_code == 204
    assert logged_in.get("/api/v1/sync/sources").json() == []
    assert logged_in.get(f"/api/v1/projects/{project_id}").json()["name"] == "Garden shed"
    assert logged_in.get(f"/api/v1/sync/sources/{source['id']}").status_code == 404


def test_source_validation_and_read_only_tokens(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    bad = logged_in.post(
        "/api/v1/sync/sources", json={"kind": "file", "locator": "../x.yaml"}, headers=headers
    )
    assert bad.status_code == 422
    token = logged_in.post(
        "/api/v1/tokens", json={"name": "ro", "read_only": True}, headers=headers
    ).json()["token"]
    with make_client(_settings(logged_in)) as api:
        bearer = {"Authorization": f"Bearer {token}"}
        assert api.get("/api/v1/sync/sources", headers=bearer).status_code == 200
        assert (
            api.post(
                "/api/v1/sync/sources", json={"kind": "file", "locator": "x.yaml"}, headers=bearer
            ).status_code
            == 403
        )


def test_export_carries_sync_sources_and_refs(logged_in: TestClient) -> None:
    headers = _csrf(logged_in)
    import_dir: Path = _settings(logged_in).effective_sync_import_dir
    import_dir.mkdir(parents=True, exist_ok=True)
    (import_dir / "shed.yaml").write_text(MANIFEST, encoding="utf-8")
    source = logged_in.post(
        "/api/v1/sync/sources", json={"kind": "file", "locator": "shed.yaml"}, headers=headers
    ).json()
    logged_in.post(f"/api/v1/sync/sources/{source['id']}/apply", json={}, headers=headers)
    document = logged_in.get("/api/v1/export").json()
    assert document["version"] == 2
    assert [s["id"] for s in document["sync_sources"]] == [source["id"]]
    assert document["sync_runs"][0]["status"] == "applied"
    assert document["projects"][0]["source_id"] == source["id"]
    assert document["projects"][0]["external_id"] == "shed"
    assert all(r["source_id"] == source["id"] for r in document["requirements"])
    assert all(i["created_by_source_id"] == source["id"] for i in document["catalog_items"])
    restored = logged_in.post("/api/v1/import?replace=true", json=document, headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["sync_sources"] == 1
    assert logged_in.get("/api/v1/export").json()["sync_sources"] == document["sync_sources"]
