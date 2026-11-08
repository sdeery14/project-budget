"""Every route except health, login and static requires the owner (ADR-005).

Two proofs: the route table declares the dependency, and the wire behaviour is
401 or 403 for an unauthenticated request, a bogus cookie and a bogus token.
"""

from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from projectbudget.services import auth
from projectbudget.web.auth import SESSION_COOKIE, require_owner

EXEMPT = {("/health", "GET"), ("/login", "GET"), ("/login", "POST")}


def app_of(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def depends_on(dependant: Dependant, target: object) -> bool:
    return any(sub.call is target or depends_on(sub, target) for sub in dependant.dependencies)


def api_routes(routes: list[Any], prefix: str = "") -> Iterator[tuple[str, str, APIRoute]]:
    """Every (path, method, route), descending into included routers and rejecting the unknown."""
    for route in routes:
        if isinstance(route, Mount):
            assert route.path == "/static", f"unexpected mount {route.path}"
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = route.include_context
            assert not context.dependencies, "include-level dependencies are not walked"
            yield from api_routes(inner.routes, prefix + context.prefix)
            continue
        assert isinstance(route, APIRoute), f"unexpected route type {route!r}"
        for method in sorted(route.methods or ()):
            yield prefix + route.path, method, route


def protected_routes(client: TestClient) -> list[tuple[str, str]]:
    return [
        (path, method)
        for path, method, _route in api_routes(app_of(client).routes)
        if (path, method) not in EXEMPT
    ]


def test_route_table_declares_require_owner(client: TestClient) -> None:
    assert protected_routes(client), "no protected routes found"
    for path, method, route in api_routes(app_of(client).routes):
        if (path, method) in EXEMPT:
            continue
        assert depends_on(route.dependant, require_owner), (
            f"{method} {path} does not depend on require_owner"
        )


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"Cookie": f"{SESSION_COOKIE}=not-a-real-session"},
        {"Authorization": "Bearer pb_not-a-real-token"},
    ],
    ids=["anonymous", "bogus-cookie", "bogus-token"],
)
def test_protected_routes_reject_unauthenticated_requests(
    client: TestClient, owner: auth.OwnerInfo, credentials: dict[str, str]
) -> None:
    for path, method in protected_routes(client):
        concrete = path.replace("{token_id}", "00000000-0000-0000-0000-000000000000")
        response = client.request(method, concrete, headers=credentials)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"
