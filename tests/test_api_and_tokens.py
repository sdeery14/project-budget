"""The token page, its HTMX fragment and the JSON twins under /api/v1 (ADR-002, ADR-005)."""

from fastapi.testclient import TestClient

from conftest import csrf_token_from, make_client
from projectbudget.settings import Settings


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_me_via_cookie_and_via_token(logged_in: TestClient) -> None:
    csrf = csrf_token_from(logged_in.get("/").text)
    assert logged_in.get("/api/v1/me").json() == {
        "username": "owner",
        "auth": "session",
        "read_only": False,
    }
    created = logged_in.post(
        "/api/v1/tokens", json={"name": "ci", "read_only": True}, headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("pb_")
    assert body["read_only"] is True
    assert body["revoked_at"] is None

    settings: Settings = logged_in.app.state.settings  # type: ignore[attr-defined]
    with make_client(settings) as anonymous:
        me = anonymous.get("/api/v1/me", headers=bearer(body["token"]))
    assert me.json() == {"username": "owner", "auth": "token", "read_only": True}


def test_read_only_tokens_cannot_change_state(logged_in: TestClient) -> None:
    csrf = csrf_token_from(logged_in.get("/").text)
    ro = logged_in.post(
        "/api/v1/tokens", json={"name": "ro", "read_only": True}, headers={"X-CSRF-Token": csrf}
    ).json()["token"]
    rw = logged_in.post(
        "/api/v1/tokens", json={"name": "rw", "read_only": False}, headers={"X-CSRF-Token": csrf}
    ).json()["token"]

    denied = logged_in.post("/api/v1/tokens", json={"name": "x"}, headers=bearer(ro))
    assert denied.status_code == 403
    assert "read-only" in denied.json()["detail"]
    # A read-write token still may not manage tokens; that needs a browser session.
    denied = logged_in.post("/api/v1/tokens", json={"name": "x"}, headers=bearer(rw))
    assert denied.status_code == 403
    assert logged_in.get("/api/v1/tokens", headers=bearer(rw)).status_code == 200


def test_revoke_via_api_then_token_is_dead(logged_in: TestClient) -> None:
    csrf = csrf_token_from(logged_in.get("/").text)
    created = logged_in.post(
        "/api/v1/tokens", json={"name": "temp"}, headers={"X-CSRF-Token": csrf}
    ).json()
    assert logged_in.get("/api/v1/me", headers=bearer(created["token"])).status_code == 200

    gone = logged_in.delete(f"/api/v1/tokens/{created['id']}", headers={"X-CSRF-Token": csrf})
    assert gone.status_code == 204
    assert logged_in.get("/api/v1/me", headers=bearer(created["token"])).status_code == 401
    listed = logged_in.get("/api/v1/tokens").json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["revoked_at"] is not None
    missing = logged_in.delete("/api/v1/tokens/nope", headers={"X-CSRF-Token": csrf})
    assert missing.status_code == 404


def test_api_validates_input(logged_in: TestClient) -> None:
    csrf = csrf_token_from(logged_in.get("/").text)
    for name in ("", "   ", "x" * 101):
        response = logged_in.post(
            "/api/v1/tokens", json={"name": name}, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 422, (name, response.status_code)
    assert logged_in.get("/api/v1/tokens").json() == []


def test_openapi_is_behind_auth_and_swagger_is_off(logged_in: TestClient) -> None:
    schema = logged_in.get("/api/v1/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/tokens" in schema.json()["paths"]
    assert logged_in.get("/docs").status_code == 404
    assert logged_in.get("/openapi.json").status_code == 404


def test_token_page_create_and_revoke(logged_in: TestClient) -> None:
    page = logged_in.get("/settings/tokens")
    assert page.status_code == 200
    assert "No tokens yet" in page.text
    csrf = csrf_token_from(page.text)

    created = logged_in.post(
        "/settings/tokens", data={"csrf_token": csrf, "name": "laptop", "read_only": "true"}
    )
    assert created.status_code == 200
    assert "Copy it now" in created.text
    assert "pb_" in created.text
    assert "read-only" in created.text

    token_id = logged_in.get("/api/v1/tokens").json()[0]["id"]
    # HTMX gets the replaced row; a plain form post gets redirected back to the page.
    fragment = logged_in.post(
        f"/settings/tokens/{token_id}/revoke",
        data={"csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert fragment.status_code == 200
    assert fragment.text.lstrip().startswith("<tr")
    assert "revoked" in fragment.text
    assert "<html" not in fragment.text

    again = logged_in.post(f"/settings/tokens/{token_id}/revoke", data={"csrf_token": csrf})
    assert again.status_code == 303
    assert again.headers["location"] == "/settings/tokens"
    unknown = logged_in.post("/settings/tokens/nope/revoke", data={"csrf_token": csrf})
    assert unknown.status_code == 404

    blank = logged_in.post("/settings/tokens", data={"csrf_token": csrf, "name": "   "})
    assert blank.status_code == 422
    assert "Token name" in blank.text
