"""``projectbudget mcp``: a stdio Model Context Protocol server over ``/api/v1`` (ADR-013 item 4).

The server is a third client of the JSON API, next to the pages and any script with a token.
Every tool calls exactly one endpoint with the bearer token from ``PROJECTBUDGET_API_TOKEN``
and returns that endpoint's JSON unchanged; the ADR-002 rule for routers applies, so there is
no business logic here and nothing from ``services`` or ``persistence`` is imported. A
read-only token gives a read-only server because the API answers writes with 403.

``TOOL_ENDPOINTS`` is the whole map; ``tests/test_mcp_server.py`` walks it against the
OpenAPI document so a tool and its endpoint cannot drift apart unnoticed.
"""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import anyio
import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from projectbudget import __version__
from projectbudget.domain.catalog import ItemKind, PurchaseStatus, ReviewState, Sharing

# The name of the variable, not a secret; ruff and bandit both pattern-match on "token".
TOKEN_ENV = "PROJECTBUDGET_API_TOKEN"  # noqa: S105  # nosec B105
URL_ENV = "PROJECTBUDGET_URL"
DEFAULT_URL = "http://127.0.0.1:8000"
API_PREFIX = "/api/v1"
TIMEOUT_SECONDS = 60.0

INSTRUCTIONS = (
    "ProjectBudget keeps one shopping backlog across the owner's projects, scheduled against "
    "a monthly budget. Read the backlog and timeline before proposing purchases; prices are "
    "decimal strings in the workspace currency, never floats. A synced project's list comes "
    "from its project-budget.yaml, so change that file and preview_sync/apply_sync instead of "
    "editing its requirements here. Errors quote the API's status and message."
)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """The one API operation a tool calls; ``path`` is relative to ``/api/v1``."""

    method: str
    path: str


TOOL_ENDPOINTS: Mapping[str, Endpoint] = {
    "list_projects": Endpoint("GET", "/projects"),
    "get_project": Endpoint("GET", "/projects/{project_id}"),
    "list_requirements": Endpoint("GET", "/projects/{project_id}/requirements"),
    "get_backlog": Endpoint("GET", "/backlog"),
    "get_timeline": Endpoint("GET", "/forecast"),
    "get_catalog_item": Endpoint("GET", "/catalog/items/{item_id}"),
    "search_catalog": Endpoint("GET", "/catalog/items"),
    "add_requirement": Endpoint("POST", "/requirements"),
    "update_requirement": Endpoint("PATCH", "/requirements/{requirement_id}"),
    "consume": Endpoint("POST", "/requirements/{requirement_id}/consume"),
    "record_purchase": Endpoint("POST", "/purchases"),
    "receive_line": Endpoint("POST", "/purchases/{purchase_id}/lines/{line_id}/receive"),
    "list_sync_sources": Endpoint("GET", "/sync/sources"),
    "preview_sync": Endpoint("POST", "/sync/sources/{source_id}/preview"),
    "apply_sync": Endpoint("POST", "/sync/sources/{source_id}/apply"),
}


class PurchaseLine(BaseModel):
    """One line of a purchase, as ``PurchaseLineIn`` in the API."""

    catalog_item_id: str
    qty: int
    unit_price: str = Field(description='Decimal string in the workspace currency, e.g. "12.50".')


class ApiClient:
    """Thin wrapper: one request per call, API errors turned into tool errors."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        self._client = client

    async def call(
        self,
        tool: str,
        path_params: Mapping[str, str] | None = None,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> str:
        endpoint = TOOL_ENDPOINTS[tool]
        path = endpoint.path.format(
            **{name: quote(value, safe="") for name, value in (path_params or {}).items()}
        )
        query = {name: value for name, value in (params or {}).items() if value is not None}
        try:
            response = await self._client.request(
                endpoint.method, API_PREFIX + path, params=query or None, json=body
            )
        except httpx2.HTTPError as exc:
            msg = f"Could not reach ProjectBudget at {self._client.base_url}: {exc}"
            raise ToolError(msg) from exc
        if response.status_code >= 400:
            raise ToolError(describe_error(response.status_code, response.text))
        return json.dumps(response.json(), separators=(",", ":"))


def describe_error(status: int, text: str) -> str:
    """What the agent reads when the API refuses: the status and the API's own message."""
    detail: Any
    try:
        detail = json.loads(text).get("detail", text)
    except (ValueError, AttributeError):
        detail = text
    if not isinstance(detail, str):
        detail = json.dumps(detail, separators=(",", ":"))
    if status == 401:
        return f"The API rejected the token (401): set {TOKEN_ENV} to a token from the Tokens page"
    if status == 403 and "read-only" in detail:
        return f"This token is read-only (403): {detail}"
    return f"HTTP {status}: {detail}"


def build_server(api: ApiClient) -> MCPServer[None]:
    """Register one tool per ``TOOL_ENDPOINTS`` entry, all closing over ``api``."""
    server: MCPServer[None] = MCPServer(
        "ProjectBudget", instructions=INSTRUCTIONS, version=__version__
    )

    @server.tool()
    async def list_projects(include_archived: bool = False) -> str:
        """List the owner's projects: id, name, notes, sync source and archive state."""
        return await api.call("list_projects", params={"include_archived": include_archived})

    @server.tool()
    async def get_project(project_id: str) -> str:
        """Get one project by id."""
        return await api.call("get_project", {"project_id": project_id})

    @server.tool()
    async def list_requirements(project_id: str, include_inactive: bool = False) -> str:
        """List a project's requirements (what it still needs), active ones by default."""
        return await api.call(
            "list_requirements",
            {"project_id": project_id},
            params={"include_inactive": include_inactive},
        )

    @server.tool()
    async def get_backlog() -> str:
        """The consolidated backlog: every catalog item still to buy, in priority order."""
        return await api.call("get_backlog")

    @server.tool()
    async def get_timeline(today: str | None = None) -> str:
        """The month-by-month timeline: which backlog items the budget affords in which month.

        ``today`` is an ISO date (YYYY-MM-DD) to forecast from; default is today.
        """
        return await api.call("get_timeline", params={"today": today})

    @server.tool()
    async def get_catalog_item(item_id: str) -> str:
        """One catalog item with its kind, unit, price, stock on hand and review state."""
        return await api.call("get_catalog_item", {"item_id": item_id})

    @server.tool()
    async def search_catalog(
        include_merged: bool = False, review_state: ReviewState | None = None
    ) -> str:
        """List catalog items; use it to find an existing item before adding a requirement."""
        return await api.call(
            "search_catalog",
            params={"include_merged": include_merged, "review_state": review_state},
        )

    @server.tool()
    async def add_requirement(
        project_id: str,
        qty: int,
        priority: int = 2,
        catalog_item_id: str | None = None,
        item_text: str | None = None,
        unit: str | None = None,
        kind: ItemKind | None = None,
        sharing: Sharing = Sharing.SHARED,
        notes: str = "",
    ) -> str:
        """Add a requirement to a project: an existing catalog item by id, or a new one by name.

        ``priority`` is 1 (most urgent) to 3. ``kind`` and ``unit`` apply only when
        ``item_text`` creates a new catalog item.
        """
        return await api.call(
            "add_requirement",
            body={
                "project_id": project_id,
                "qty": qty,
                "priority": priority,
                "catalog_item_id": catalog_item_id,
                "item_text": item_text,
                "unit": unit,
                "kind": kind,
                "sharing": sharing,
                "notes": notes,
            },
        )

    @server.tool()
    async def update_requirement(
        requirement_id: str,
        qty: int | None = None,
        priority: int | None = None,
        sort_order: int | None = None,
        sharing: Sharing | None = None,
        notes: str | None = None,
    ) -> str:
        """Change a requirement's quantity, priority, order, sharing or notes; omitted fields stay."""
        return await api.call(
            "update_requirement",
            {"requirement_id": requirement_id},
            body={
                "qty": qty,
                "priority": priority,
                "sort_order": sort_order,
                "sharing": sharing,
                "notes": notes,
            },
        )

    @server.tool()
    async def consume(
        requirement_id: str, qty: int, occurred_on: str | None = None, note: str = ""
    ) -> str:
        """Record that a project used ``qty`` of a requirement's item from stock (ISO date optional)."""
        return await api.call(
            "consume",
            {"requirement_id": requirement_id},
            body={"qty": qty, "occurred_on": occurred_on, "note": note},
        )

    @server.tool()
    async def record_purchase(
        lines: list[PurchaseLine],
        vendor: str = "",
        ordered_on: str | None = None,
        status: PurchaseStatus = PurchaseStatus.RECEIVED,
        shipping: str | None = None,
        tax: str | None = None,
        notes: str = "",
    ) -> str:
        """Record a purchase of one or more catalog items at real prices (decimal strings).

        ``status`` received puts the lines into stock; ordered leaves them on order until
        ``receive_line``. ``ordered_on`` is an ISO date.
        """
        return await api.call(
            "record_purchase",
            body={
                "lines": [line.model_dump() for line in lines],
                "vendor": vendor,
                "ordered_on": ordered_on,
                "status": status,
                "shipping": shipping,
                "tax": tax,
                "notes": notes,
            },
        )

    @server.tool()
    async def receive_line(
        purchase_id: str, line_id: str, qty: int, occurred_on: str | None = None
    ) -> str:
        """Receive part or all of an ordered purchase line into stock (ISO date optional)."""
        return await api.call(
            "receive_line",
            {"purchase_id": purchase_id, "line_id": line_id},
            body={"qty": qty, "occurred_on": occurred_on},
        )

    @server.tool()
    async def list_sync_sources() -> str:
        """List the manifest sources (repositories and import files) the owner syncs from."""
        return await api.call("list_sync_sources")

    @server.tool()
    async def preview_sync(
        source_id: str, manifest_text: str | None = None, force: bool = False
    ) -> str:
        """Fetch a source's manifest (or take ``manifest_text``) and show the plan without applying."""
        return await api.call(
            "preview_sync",
            {"source_id": source_id},
            body={"manifest_text": manifest_text, "force": force},
        )

    @server.tool()
    async def apply_sync(
        source_id: str,
        manifest_text: str | None = None,
        force: bool = False,
        expected_content_hash: str | None = None,
        expected_plan_digest: str | None = None,
    ) -> str:
        """Apply a source's manifest. Pass the preview's content hash and plan digest to refuse a changed source."""
        return await api.call(
            "apply_sync",
            {"source_id": source_id},
            body={
                "manifest_text": manifest_text,
                "force": force,
                "expected_content_hash": expected_content_hash,
                "expected_plan_digest": expected_plan_digest,
            },
        )

    return server


def make_http_client(base_url: str, token: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": f"projectbudget-mcp/{__version__}",
        },
        timeout=TIMEOUT_SECONDS,
    )


@asynccontextmanager
async def connected_server(base_url: str, token: str) -> AsyncIterator[MCPServer[None]]:
    """A server whose tools talk to ``base_url`` with ``token``; the client closes with it."""
    async with make_http_client(base_url, token) as client:
        yield build_server(ApiClient(client))


def serve_stdio(base_url: str, token: str) -> None:
    """Run until the client closes stdin. Nothing but protocol goes to stdout."""

    async def main() -> None:
        async with connected_server(base_url, token) as server:
            await server.run_stdio_async()

    anyio.run(main)
