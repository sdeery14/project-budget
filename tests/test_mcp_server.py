"""``projectbudget mcp`` is a thin client of ``/api/v1`` (#83, ADR-013 item 4).

The first test walks the tool list against the OpenAPI document, so a tool and its endpoint
cannot drift apart; the rest drive the server against a live app with real tokens.
"""

import json
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from datetime import date
from typing import Any

import anyio
import pytest
import uvicorn
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from typer.testing import CliRunner

from projectbudget import mcp_server
from projectbudget.cli.main import app
from projectbudget.domain.principal import Principal
from projectbudget.fixtures.canonical import CanonicalWorkspace, seed_canonical
from projectbudget.mcp_server import TOOL_ENDPOINTS, ApiClient, build_server, connected_server
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import auth, tokens
from projectbudget.settings import Settings
from projectbudget.web.app import create_app

# The ADR keeps these out of the agent's reach: token management stays in the browser, the
# workspace document and the budget plan are the owner's, and nothing deletes.
FORBIDDEN_PREFIXES = ("/tokens", "/export", "/import", "/budget", "/backlog/order")


def run[T](scenario: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run a coroutine to completion in a worker thread.

    The Playwright session fixture under ``tests/e2e`` keeps an event loop running in the main
    thread for the rest of the session, so ``anyio.run`` there would refuse to start another.
    """
    outcome: list[T] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            outcome.append(anyio.run(scenario))
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=target, name="mcp-test-scenario")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return outcome[0]


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Follow ``$ref`` and ``anyOf [X, null]`` down to the object schema."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[1]
        return _resolve(components[name], components)
    if "anyOf" in schema:
        (inner,) = [s for s in schema["anyOf"] if s != {"type": "null"}]
        return _resolve(inner, components)
    return schema


def test_every_tool_mirrors_one_api_operation_exactly(app_settings: Settings) -> None:
    """Tool arguments are the operation's path and query parameters plus its body fields."""
    openapi = create_app(app_settings).openapi()
    components = openapi["components"]["schemas"]
    server = build_server(ApiClient(mcp_server.make_http_client("http://unused", "pb_x")))
    tools = {tool.name: tool for tool in run(server.list_tools)}
    assert set(tools) == set(TOOL_ENDPOINTS)

    for name, endpoint in TOOL_ENDPOINTS.items():
        assert endpoint.method in ("GET", "POST", "PATCH"), name
        assert not endpoint.path.startswith(FORBIDDEN_PREFIXES), name
        operation = openapi["paths"][mcp_server.API_PREFIX + endpoint.path][endpoint.method.lower()]
        expected = {p["name"] for p in operation.get("parameters", [])}
        required = {p["name"] for p in operation.get("parameters", []) if p.get("required")}
        if "requestBody" in operation:
            body = _resolve(
                operation["requestBody"]["content"]["application/json"]["schema"], components
            )
            expected |= set(body["properties"])
            required |= set(body.get("required", []))
        schema = tools[name].input_schema
        assert set(schema.get("properties", {})) == expected, name
        assert set(schema.get("required", [])) == required, name

    lines = tools["record_purchase"].input_schema["$defs"]["PurchaseLine"]
    assert set(lines["properties"]) == set(components["PurchaseLineIn"]["properties"])
    assert set(lines["required"]) == set(components["PurchaseLineIn"]["required"])


# --- a live app, real tokens -------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def api_url(app_settings: Settings, owner: auth.OwnerInfo) -> Iterator[str]:
    """The app served by uvicorn in a thread, so the server is exercised over real HTTP."""
    port = _free_port()
    config = uvicorn.Config(
        create_app(app_settings), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mcp-test-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def workspace(session_factory: SessionFactory, principal: Principal) -> CanonicalWorkspace:
    return seed_canonical(session_factory, principal, as_of=date(2026, 11, 1))


def _token(factory: SessionFactory, principal: Principal, *, read_only: bool) -> str:
    return tokens.issue_token(factory, principal, "agent", read_only=read_only).secret


async def _call(server: MCPServer[None], name: str, **arguments: Any) -> Any:
    result = await server.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    assert not result.is_error, result
    (content,) = result.content
    assert isinstance(content, TextContent)
    return json.loads(content.text)


async def _on_hand(server: MCPServer[None], item_id: str) -> int:
    backlog = await _call(server, "get_backlog")
    (row,) = [row for row in backlog["rows"] if row["item_id"] == item_id]
    return int(row["on_hand"])


def test_reads_and_writes_through_a_read_write_token(
    api_url: str,
    session_factory: SessionFactory,
    principal: Principal,
    workspace: CanonicalWorkspace,
) -> None:
    token = _token(session_factory, principal, read_only=False)

    async def scenario() -> None:
        async with connected_server(api_url, token) as server:
            projects = await _call(server, "list_projects")
            assert {p["name"] for p in projects} == {"Workbench", "Garden gate"}
            gate = await _call(server, "get_project", project_id=workspace.garden_gate.id)
            assert gate["name"] == "Garden gate"

            added = await _call(
                server,
                "add_requirement",
                project_id=workspace.garden_gate.id,
                qty=2,
                priority=1,
                item_text="Gate latch",
            )
            listed = await _call(server, "list_requirements", project_id=workspace.garden_gate.id)
            assert added["id"] in {r["id"] for r in listed}
            backlog = await _call(server, "get_backlog")
            assert "Gate latch" in {row["name"] for row in backlog["rows"] + backlog["unpriced"]}
            unreviewed = await _call(server, "search_catalog", review_state="unreviewed")
            assert "Gate latch" in {item["name"] for item in unreviewed}
            changed = await _call(
                server, "update_requirement", requirement_id=added["id"], qty=3, notes="two spare"
            )
            assert (changed["qty"], changed["notes"]) == (3, "two spare")

            bolts = await _call(server, "get_catalog_item", item_id=workspace.bolts.id)
            assert bolts["slug"] == "m8-carriage-bolt-80"
            before = await _on_hand(server, workspace.bolts.id)
            purchase = await _call(
                server,
                "record_purchase",
                lines=[{"catalog_item_id": workspace.bolts.id, "qty": 4, "unit_price": "0.60"}],
                vendor="Hardware store",
                status="ordered",
            )
            assert purchase["status"] == "ordered"
            (line,) = purchase["lines"]
            received = await _call(
                server, "receive_line", purchase_id=purchase["id"], line_id=line["id"], qty=4
            )
            assert received["status"] == "received"
            assert await _on_hand(server, workspace.bolts.id) == before + 4
            movement = await _call(
                server, "consume", requirement_id=workspace.gate_bolts.id, qty=1, note="first post"
            )
            assert movement["delta_qty"] == -1
            assert await _on_hand(server, workspace.bolts.id) == before + 3

            timeline = await _call(server, "get_timeline")
            assert timeline["optimistic"]["months"]
            assert await _call(server, "list_sync_sources") == []
            with pytest.raises(ToolError, match="HTTP 404"):
                await _call(server, "preview_sync", source_id="no-such-source")

    run(scenario)


def test_a_read_only_token_reads_but_every_write_is_refused(
    api_url: str,
    session_factory: SessionFactory,
    principal: Principal,
    workspace: CanonicalWorkspace,
) -> None:
    token = _token(session_factory, principal, read_only=True)

    async def scenario() -> None:
        async with connected_server(api_url, token) as server:
            assert len(await _call(server, "list_projects")) == 2
            with pytest.raises(ToolError, match=r"read-only \(403\)"):
                await _call(
                    server,
                    "add_requirement",
                    project_id=workspace.garden_gate.id,
                    qty=1,
                    item_text="Gate latch",
                )
            with pytest.raises(ToolError, match=r"read-only \(403\)"):
                await _call(server, "consume", requirement_id=workspace.gate_bolts.id, qty=1)

    run(scenario)


def test_a_bad_token_and_an_unreachable_server_are_explained(api_url: str) -> None:
    async def scenario() -> None:
        async with connected_server(api_url, "pb_not_a_token") as server:
            with pytest.raises(ToolError, match=r"rejected the token \(401\)"):
                await _call(server, "list_projects")
        async with connected_server("http://127.0.0.1:9", "pb_x") as server:
            with pytest.raises(
                ToolError, match=r"Could not reach ProjectBudget at http://127\.0\.0\.1:9"
            ):
                await _call(server, "list_projects")

    run(scenario)


# --- the command ---------------------------------------------------------------------------


def test_the_command_refuses_to_start_without_a_token() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["mcp", "--help"]).exit_code == 0
    result = runner.invoke(app, ["mcp"], env={mcp_server.TOKEN_ENV: ""})
    assert result.exit_code == 2, result.output
    assert mcp_server.TOKEN_ENV in result.output
    assert "Tokens page" in result.output


def test_the_command_serves_the_protocol_on_stdio(
    api_url: str,
    session_factory: SessionFactory,
    principal: Principal,
    workspace: CanonicalWorkspace,
) -> None:
    """A real client process: ``projectbudget mcp`` spawned with the token in its environment."""
    token = _token(session_factory, principal, read_only=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from projectbudget.cli.main import app; app()", "mcp", "--url", api_url],
        env={**os.environ, mcp_server.TOKEN_ENV: token},
    )

    async def scenario() -> None:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info.name == "ProjectBudget"
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == set(TOOL_ENDPOINTS)
            result = await session.call_tool("get_project", {"project_id": workspace.workbench.id})
            assert not result.is_error
            (content,) = result.content
            assert isinstance(content, TextContent)
            assert json.loads(content.text)["name"] == "Workbench"
            refused = await session.call_tool(
                "add_requirement",
                {"project_id": workspace.workbench.id, "qty": 1, "item_text": "Gate latch"},
            )
            assert refused.is_error
            (content,) = refused.content
            assert isinstance(content, TextContent)
            assert "read-only (403)" in content.text

    run(scenario)
