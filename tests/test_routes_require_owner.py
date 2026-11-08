"""Every route except health, login and static requires the owner (ADR-005).

Two proofs: the route table declares the dependency (on the route itself or on
a router it was included through), and the wire behaviour is 401 or 403 for an
unauthenticated request, a bogus cookie and a bogus token.
"""

import re
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.params import Depends
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from projectbudget.services import auth
from projectbudget.web.auth import SESSION_COOKIE, require_owner

EXEMPT = {("/health", "GET"), ("/login", "GET"), ("/login", "POST")}
PATH_PARAM = re.compile(r"\{[^}]+\}")
NIL_UUID = "00000000-0000-0000-0000-000000000000"


def app_of(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def depends_on(dependant: Dependant, target: object) -> bool:
    return any(sub.call is target or depends_on(sub, target) for sub in dependant.dependencies)


def api_routes(
    routes: list[Any], prefix: str = "", inherited: tuple[Depends, ...] = ()
) -> Iterator[tuple[str, str, APIRoute, tuple[Depends, ...]]]:
    """Every (path, method, route, include-level dependencies), descending into included routers."""
    for route in routes:
        if isinstance(route, Mount):
            assert route.path == "/static", f"unexpected mount {route.path}"
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = route.include_context
            deps = inherited + tuple(context.dependencies or ())
            yield from api_routes(inner.routes, prefix + context.prefix, deps)
            continue
        assert isinstance(route, APIRoute), f"unexpected route type {route!r}"
        for method in sorted(route.methods or ()):
            yield prefix + route.path, method, route, inherited


def protected_routes(client: TestClient) -> list[tuple[str, str]]:
    return [
        (path, method)
        for path, method, _route, _deps in api_routes(app_of(client).routes)
        if (path, method) not in EXEMPT
    ]


def test_route_table_declares_require_owner(client: TestClient) -> None:
    assert protected_routes(client), "no protected routes found"
    for path, method, route, inherited in api_routes(app_of(client).routes):
        if (path, method) in EXEMPT:
            continue
        declared = depends_on(route.dependant, require_owner) or any(
            dep.dependency is require_owner for dep in inherited
        )
        assert declared, f"{method} {path} does not depend on require_owner"


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
        concrete = PATH_PARAM.sub(NIL_UUID, path)
        response = client.request(method, concrete, headers=credentials)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"
