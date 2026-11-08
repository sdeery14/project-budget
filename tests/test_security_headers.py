"""Security headers on every response (RFC v3 §7)."""

from fastapi.testclient import TestClient

from projectbudget.services import auth


def test_pages_carry_the_headers_and_are_not_cached(
    client: TestClient, owner: auth.OwnerInfo
) -> None:
    response = client.get("/login")
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_static_assets_are_cacheable_but_still_protected(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "content-security-policy" in response.headers
    assert response.headers.get("cache-control") != "no-store"
    htmx = client.get("/static/vendor/htmx/htmx.min.js")
    assert htmx.status_code == 200
    assert 'version:"2.0.11"' in htmx.text


def test_error_responses_carry_the_headers_too(client: TestClient) -> None:
    rejected = client.post("/login", data={}, headers={"Origin": "https://evil.example"})
    assert rejected.status_code == 403
    assert "content-security-policy" in rejected.headers
    health = client.get("/health")
    assert "content-security-policy" in health.headers
