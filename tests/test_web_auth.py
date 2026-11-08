"""Login, logout, sessions, CSRF and the Origin check over HTTP (ADR-005)."""

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ORIGIN, OWNER_PASSWORD, OWNER_USERNAME, csrf_token_from, log_in, make_client
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import auth
from projectbudget.settings import Settings
from projectbudget.web.auth import SESSION_COOKIE


def session_factory_of(client: TestClient) -> SessionFactory:
    factory: SessionFactory = client.app.state.session_factory  # type: ignore[attr-defined]
    return factory


def test_without_an_owner_the_app_refuses_to_serve(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 503
    assert "projectbudget init" in page.text
    assert client.get("/login").status_code == 503
    api = client.get("/api/v1/me")
    assert api.status_code == 503
    assert "init" in api.json()["detail"]
    assert client.get("/health").status_code == 200


def test_login_page_renders(client: TestClient, owner: auth.OwnerInfo) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert 'name="username"' in response.text
    assert "plain HTTP" not in response.text


def test_wrong_password_is_401(client: TestClient, owner: auth.OwnerInfo) -> None:
    response = client.post("/login", data={"username": OWNER_USERNAME, "password": "nope"})
    assert response.status_code == 401
    assert "Wrong username or password" in response.text
    assert SESSION_COOKIE not in response.cookies


def test_login_sets_a_hardened_cookie_and_redirects(
    client: TestClient, owner: auth.OwnerInfo
) -> None:
    response = client.post(
        "/login",
        data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD, "next": "/settings/tokens"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings/tokens"
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for attribute in ("httponly", "secure", "samesite=lax", "path=/", "max-age="):
        assert attribute in cookie, cookie

    home = client.get("/")
    assert home.status_code == 200
    assert OWNER_USERNAME in home.text
    assert client.get("/login").status_code == 303


def test_next_must_be_a_relative_path(client: TestClient, owner: auth.OwnerInfo) -> None:
    for bad in ("//evil.example/x", "https://evil.example/", "relative", ""):
        response = client.post(
            "/login", data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD, "next": bad}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/", bad


def test_login_rotates_the_session(client: TestClient, owner: auth.OwnerInfo) -> None:
    log_in(client)
    first = client.cookies[SESSION_COOKIE]
    log_in(client)
    second = client.cookies[SESSION_COOKIE]
    assert first != second
    stale = client.get("/", headers={"Cookie": f"{SESSION_COOKIE}={first}"})
    assert stale.status_code == 401


def test_logout_revokes_and_clears(logged_in: TestClient) -> None:
    token = csrf_token_from(logged_in.get("/").text)
    response = logged_in.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert logged_in.get("/").status_code == 401


def test_state_changes_need_the_session_csrf_token(logged_in: TestClient) -> None:
    assert logged_in.post("/logout").status_code == 403
    assert logged_in.post("/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert logged_in.post("/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    token = csrf_token_from(logged_in.get("/").text)
    assert logged_in.post("/logout", headers={"X-CSRF-Token": token}).status_code == 303


def test_cross_origin_state_changes_are_rejected(client: TestClient, owner: auth.OwnerInfo) -> None:
    form = {"username": OWNER_USERNAME, "password": OWNER_PASSWORD}
    no_origin = client.post("/login", data=form, headers={"Origin": ""})
    assert no_origin.status_code == 403
    evil = client.post("/login", data=form, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    null = client.post("/login", data=form, headers={"Origin": "null"})
    assert null.status_code == 403
    # Referer is an acceptable fallback when Origin is absent.
    referer = client.post(
        "/login", data=form, headers={"Origin": "", "Referer": f"{ORIGIN}/login?next=/"}
    )
    assert referer.status_code == 303
    api = client.post(
        "/api/v1/tokens", json={"name": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert api.status_code == 403
    assert api.json()["detail"].startswith("Cross-origin")


def test_unauthenticated_html_gets_the_login_page_with_next(
    client: TestClient, owner: auth.OwnerInfo
) -> None:
    response = client.get("/settings/tokens?page=2")
    assert response.status_code == 401
    assert 'name="next" value="/settings/tokens?page=2"' in response.text


def test_unauthenticated_htmx_gets_a_redirect_header(
    client: TestClient, owner: auth.OwnerInfo
) -> None:
    response = client.get("/", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_unauthenticated_api_gets_json_401(client: TestClient, owner: auth.OwnerInfo) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Authentication required"}
    bogus = client.get("/api/v1/me", headers={"Authorization": "Bearer pb_bogus"})
    assert bogus.status_code == 401


def test_lockout_over_http(client: TestClient, owner: auth.OwnerInfo) -> None:
    for _ in range(auth.LOGIN_MAX_FAILURES):
        assert (
            client.post("/login", data={"username": OWNER_USERNAME, "password": "nope"}).status_code
            == 401
        )
    locked = client.post("/login", data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    assert "Too many failed attempts" in locked.text


def test_secure_cookie_can_be_turned_off_for_plain_http(migrated_url: str, tmp_path: Path) -> None:
    settings = Settings(database_url=migrated_url, data_dir=tmp_path, secure_cookies=False)
    with make_client(settings, base_url="http://testserver") as client:
        auth.create_owner(session_factory_of(client), OWNER_USERNAME, OWNER_PASSWORD)
        assert "plain HTTP" not in client.get("/login").text
        response = client.post(
            "/login", data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD}
        )
        assert response.status_code == 303
        assert "secure" not in response.headers["set-cookie"].lower()
        assert "strict-transport-security" not in response.headers


def test_plain_http_from_a_non_local_host_shows_a_warning(
    migrated_url: str, tmp_path: Path
) -> None:
    settings = Settings(database_url=migrated_url, data_dir=tmp_path)
    with make_client(settings, base_url="http://nas.local") as client:
        auth.create_owner(session_factory_of(client), OWNER_USERNAME, OWNER_PASSWORD)
        assert "plain HTTP" in client.get("/login").text
    with make_client(settings, base_url="http://localhost") as client:
        assert "plain HTTP" not in client.get("/login").text
