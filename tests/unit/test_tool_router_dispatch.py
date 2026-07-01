"""Unit tests for ``tool_router.invoke_tool`` dispatch (audit C2 / ROUTER-2).

C2 fix: ``invoke_tool`` must reach the backend tools — including the async,
``ctx``-requiring destructive ops tools — by dispatching through the owning
backend's FastMCP tool manager (``backend._tool_manager.call_tool(name, args,
context=ctx)``) instead of calling ``tool.fn(**args)`` and running coroutines
on the already-running loop.

These tests build a tiny in-process FastMCP backend with three tool shapes:
  (a) a plain sync tool,
  (b) an async tool (no ctx),
  (c) an async tool whose first param is ``ctx: Context`` (FastMCP strips it
      from the published schema and injects it at call time).
They register that backend exactly like ``_load_all_backends`` does (name ->
tool index + name -> owning server) and prove ``invoke_tool`` returns results
or structured ``{"error": ...}`` dicts — all without any network access.

``invoke_tool`` is async, so each test drives it via ``asyncio.run`` (the repo
has no pytest-asyncio plugin; this mirrors the helper in test_mcp_middleware).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.fastmcp import Context, FastMCP

import mcp_servers.tool_router as router
from mcp_servers.shared import IDEMPOTENT_WRITE, READ_ONLY

# ---------------------------------------------------------------------------
# A tiny backend that mirrors the three tool shapes the router must dispatch.
# ---------------------------------------------------------------------------


def _build_backend() -> FastMCP:
    srv = FastMCP("test-backend")

    @srv.tool(annotations=READ_ONLY)
    def sync_echo(value: int) -> dict[str, Any]:
        return {"kind": "sync", "value": value}

    @srv.tool(annotations=READ_ONLY)
    async def async_echo(value: int) -> dict[str, Any]:
        return {"kind": "async", "value": value}

    @srv.tool(annotations=READ_ONLY)
    def optional_limit(limit: int = 5) -> dict[str, Any]:
        return {"limit": limit}

    @srv.tool()
    async def async_ctx_echo(ctx: Context, value: int) -> dict[str, Any]:
        # The destructive ops tools take ``ctx: Context`` first and would never
        # receive it via ``tool.fn(**args)``. Prove FastMCP injected it.
        return {"kind": "async_ctx", "value": value, "ctx_injected": ctx is not None}

    @srv.tool()
    def boom() -> dict[str, Any]:
        raise RuntimeError("kaboom")

    @srv.tool(annotations=IDEMPOTENT_WRITE)
    def write_echo(value: int) -> dict[str, Any]:
        return {"kind": "write", "value": value}

    return srv


@pytest.fixture
def wired_router(monkeypatch):
    """Point the router's name->tool / name->server maps at the test backend.

    Stubs ``_load_all_backends`` so dispatch never imports the real backends
    (and never touches Redis/Ollama/the live API)."""
    backend = _build_backend()
    tools = dict(backend._tool_manager._tools)
    servers = {name: backend for name in tools}

    monkeypatch.setattr(router, "_tool_index", tools, raising=True)
    monkeypatch.setattr(router, "_tool_servers", servers, raising=True)
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {name: "test-backend" for name in tools},
        raising=True,
    )
    # Maps are already populated; make the lazy loader a no-op so it doesn't
    # clobber them by importing the real backend modules.
    monkeypatch.setattr(router, "_load_all_backends", lambda: None, raising=True)
    return backend


def _invoke(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Drive the async router.invoke_tool to completion.

    Outside a live MCP request the injected Context has ``request_context=None``;
    the test tools never call ctx methods, so a bare Context is sufficient.
    """
    ctx = router.mcp.get_context()
    return asyncio.run(router.invoke_tool(ctx, name, arguments))


def _invoke_read(name: str, arguments: dict[str, Any] | None = None) -> Any:
    ctx = router.mcp.get_context()
    return asyncio.run(router.invoke_read_tool(ctx, name, arguments))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_sync_tool(self, wired_router):
        out = _invoke("sync_echo", {"value": 7})
        assert out == {"kind": "sync", "value": 7}

    def test_async_tool(self, wired_router):
        out = _invoke("async_echo", {"value": 9})
        assert out == {"kind": "async", "value": 9}

    def test_read_only_dispatcher_allows_read_only_tool(self, wired_router):
        out = _invoke_read("sync_echo", {"value": 7})
        assert out == {"kind": "sync", "value": 7}

    def test_dispatcher_strips_nested_null_arguments(self, wired_router):
        out = _invoke_read("optional_limit", {"limit": None})
        assert out == {"limit": 5}

    def test_read_only_dispatcher_blocks_write_tool(self, wired_router):
        out = _invoke_read("write_echo", {"value": 7})
        assert out["status"] == "blocked"
        assert "not read-only" in out["error"]

    def test_async_ctx_tool_gets_context_injected(self, wired_router):
        # The core C2 regression: a ctx-requiring async tool must run AND
        # receive an injected Context (it never would via tool.fn(**args)).
        out = _invoke("async_ctx_echo", {"value": 3})
        assert out == {"kind": "async_ctx", "value": 3, "ctx_injected": True}

    def test_optional_write_dispatch_blocked_when_product_access_read_only(
        self,
        wired_router,
        monkeypatch,
    ):
        monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
        router._tool_backend_names["write_echo"] = "clearpass-core"

        out = _invoke("write_echo", {"value": 7})

        assert out["status"] == "blocked"
        assert "CENTRALMCP_PRODUCT_ACCESS=read-only" in out["error"]

    def test_ctx_stripped_from_published_schema(self, wired_router):
        # FastMCP must hide ``ctx`` from the schema callers see, so invoke_tool
        # only ever forwards real arguments.
        params = wired_router._tool_manager._tools["async_ctx_echo"].parameters
        props = (params.get("properties") or {})
        assert "ctx" not in props
        assert "value" in props


# ---------------------------------------------------------------------------
# Structured errors — never a silent failure / raised exception.
# ---------------------------------------------------------------------------


class TestStructuredErrors:
    def test_unknown_tool(self, wired_router):
        out = _invoke("does_not_exist", {})
        assert isinstance(out, dict)
        assert "error" in out
        assert "does_not_exist" in out["error"]

    def test_tool_raises_returns_error_dict(self, wired_router):
        out = _invoke("boom")
        assert isinstance(out, dict)
        assert "error" in out
        # FastMCP wraps the failure in ToolError; the original message survives.
        assert "kaboom" in out["error"]

    def test_bad_arguments_returns_error_dict(self, wired_router):
        # Missing required arg -> FastMCP validation error, surfaced structured.
        out = _invoke("sync_echo", {})
        assert isinstance(out, dict)
        assert "error" in out
