"""Real protocol-boundary MCP E2E smoke tests (stable SDK 1.28.1).

Every other test in this suite drives tools through
``FastMCP._tool_manager.call_tool(...)`` directly -- useful and fast, but
it never serializes a request/response across the actual JSON-RPC
boundary a real MCP client uses. These tests do: they build a real
``ClientSession`` wired to a real server via the SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``), so
``initialize``/``list_tools``/``call_tool`` go through the SDK's real
request/response (de)serialization, not just a Python function call.

Deliberately does NOT import ``mcp_servers.tool_router`` (that pulls in an
embedding model / lance / redis backend selection at import time) -- a
small standalone FastMCP server exercising the same shapes the router's
tools produce (read tool, blocked-write envelope, raised-exception error,
elicitation) is enough to prove the protocol boundary itself works with
this repo's real middleware chain installed.

This repo has no pytest-asyncio/anyio pytest plugin installed (see other
async tests in this suite), so each test wraps its async body in
``asyncio.run(...)`` rather than using an ``@pytest.mark.anyio`` marker.

Do not upgrade to the SDK v2 prerelease -- these tests pin to whatever
``mcp[cli]>=1.28.1`` (the stable line pyproject.toml already requires)
resolves to.
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult
from pydantic import BaseModel

from mcp_servers._middleware import (
    NullStripMiddleware,
    ResponseEnvelopeMiddleware,
    install_middleware,
)
from mcp_servers.shared import DESTRUCTIVE, READ_ONLY, enforce_platform_write


def _build_server() -> FastMCP:
    srv = FastMCP("e2e-smoke-server")

    @srv.tool(annotations=READ_ONLY)
    def list_devices(limit: int = 10) -> dict:
        """List devices (read-only)."""
        return {"items": [{"serial": "CN1"}, {"serial": "CN2"}][:limit]}

    @srv.tool(annotations=DESTRUCTIVE)
    def reboot_device(serial_number: str) -> dict:
        """Reboot a device -- gated by the real per-platform write helper."""
        blocked = enforce_platform_write("mist", "reboot_device")
        if blocked:
            return blocked
        return {"status": "rebooting", "serial_number": serial_number}

    @srv.tool()
    def boom() -> dict:
        """Always raises -- protocol-boundary error-envelope path."""
        raise RuntimeError("simulated tool failure")

    class ConfirmSchema(BaseModel):
        confirm: bool

    @srv.tool()
    async def confirm_reboot(ctx: Context, serial_number: str) -> dict:
        """Elicits a yes/no confirmation before "rebooting"."""
        result = await ctx.elicit(
            message=f"Reboot {serial_number}?", schema=ConfirmSchema
        )
        if result.action != "accept" or not result.data or not result.data.confirm:
            return {"status": "cancelled", "serial_number": serial_number}
        return {"status": "rebooting", "serial_number": serial_number}

    install_middleware(
        srv, [NullStripMiddleware(), ResponseEnvelopeMiddleware()]
    )
    return srv


def _text_payload(call_result) -> dict:
    """Extract and JSON-parse the first TextContent block of a CallToolResult."""
    assert call_result.content, "expected at least one content block"
    block = call_result.content[0]
    return json.loads(block.text)


def test_initialize_and_list_tools_over_real_protocol_boundary():
    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.list_tools()

    result = asyncio.run(_run())

    names = {tool.name for tool in result.tools}
    assert {"list_devices", "reboot_device", "boom", "confirm_reboot"} <= names

    # Annotations survive the wire round trip.
    by_name = {tool.name: tool for tool in result.tools}
    assert by_name["list_devices"].annotations.readOnlyHint is True
    assert by_name["reboot_device"].annotations.destructiveHint is True


def test_read_tool_call_round_trips_real_json_rpc():
    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("list_devices", {"limit": 1})

    result = asyncio.run(_run())

    assert result.isError is False
    payload = _text_payload(result)
    assert payload == {"items": [{"serial": "CN1"}]}


def test_null_strip_middleware_runs_across_the_protocol_boundary():
    """A real client sends ``{"limit": None}`` (JSON null) for "use the
    default" -- NullStripMiddleware must strip it before Pydantic validation
    sees it, exactly as it does for router calls in production."""

    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("list_devices", {"limit": None})

    result = asyncio.run(_run())

    assert result.isError is False
    payload = _text_payload(result)
    assert payload == {"items": [{"serial": "CN1"}, {"serial": "CN2"}]}


def test_blocked_write_envelope_survives_the_wire():
    """enforce_platform_write's blocked dict must reach the client as the
    ResponseEnvelopeMiddleware {ok, status, data, message, tool} shape --
    proving the whole middleware chain, not just the raw tool function,
    runs on a real protocol-boundary call."""

    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool(
                "reboot_device", {"serial_number": "CN1"}
            )

    result = asyncio.run(_run())

    assert result.isError is False  # envelope, not a transport-level error
    payload = _text_payload(result)
    assert payload["ok"] is False
    assert payload["status"] == 500  # ResponseEnvelopeMiddleware's generic error code
    assert payload["data"]["status"] == "blocked"
    assert payload["tool"] == "reboot_device"
    assert "CENTRALMCP_MIST_WRITES" in payload["message"]


def test_allowed_write_executes_over_the_wire(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_MIST_WRITES", "1")

    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool(
                "reboot_device", {"serial_number": "CN1"}
            )

    result = asyncio.run(_run())

    assert result.isError is False
    payload = _text_payload(result)
    assert payload == {"status": "rebooting", "serial_number": "CN1"}


def test_raised_exception_becomes_structured_protocol_error():
    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("boom", {})

    result = asyncio.run(_run())

    assert result.isError is True
    assert "simulated tool failure" in result.content[0].text


def test_unknown_tool_call_returns_a_protocol_error():
    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            return await session.call_tool("does_not_exist", {})

    result = asyncio.run(_run())

    assert result.isError is True


def test_elicitation_accept_flow_over_real_protocol_boundary():
    """Drives ctx.elicit() through a real elicitation_callback on the
    client session -- the actual SDK request/response round trip for a
    server-initiated elicitation, not a mocked ctx.elicit()."""

    async def auto_accept(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(
            server, elicitation_callback=auto_accept
        ) as session:
            await session.initialize()
            return await session.call_tool(
                "confirm_reboot", {"serial_number": "CN1"}
            )

    result = asyncio.run(_run())

    assert result.isError is False
    payload = _text_payload(result)
    assert payload == {"status": "rebooting", "serial_number": "CN1"}


def test_elicitation_decline_flow_over_real_protocol_boundary():
    async def auto_decline(context, params):
        return ElicitResult(action="decline")

    async def _run():
        server = _build_server()
        async with create_connected_server_and_client_session(
            server, elicitation_callback=auto_decline
        ) as session:
            await session.initialize()
            return await session.call_tool(
                "confirm_reboot", {"serial_number": "CN1"}
            )

    result = asyncio.run(_run())

    assert result.isError is False
    payload = _text_payload(result)
    assert payload["ok"] is False
    assert payload["status"] == 409  # ResponseEnvelopeMiddleware's "cancelled" mapping
    assert payload["data"] == {"status": "cancelled", "serial_number": "CN1"}
