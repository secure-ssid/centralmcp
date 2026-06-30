from __future__ import annotations

import asyncio

import mcp_servers.tool_router as router


def test_build_backends_default_has_core_only(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)
    backends = router._build_backends()
    assert "aruba-config" in backends
    assert "clearpass-core" not in backends


def test_build_backends_enables_clearpass(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)
    monkeypatch.setenv("CENTRALMCP_PRODUCTS", "clearpass")
    backends = router._build_backends()
    assert backends.get("clearpass-core") == "mcp_servers.clearpass"


def test_build_backends_enables_multiple_products(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)
    monkeypatch.setenv("CENTRALMCP_PRODUCTS", "clearpass,mist,apstra,aos8,edgeconnect")
    backends = router._build_backends()
    assert backends.get("clearpass-core") == "mcp_servers.clearpass"
    assert backends.get("mist-core") == "mcp_servers.mist"
    assert backends.get("apstra-core") == "mcp_servers.apstra"
    assert backends.get("aos8-core") == "mcp_servers.aos8"
    assert backends.get("edgeconnect-core") == "mcp_servers.edgeconnect"


def test_build_backends_toolsets_narrow_core(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.setenv("CENTRALMCP_TOOLSETS", "monitoring,rag")
    backends = router._build_backends()
    assert set(backends) == {"aruba-monitoring", "aruba-rag"}


def test_build_backends_toolsets_can_enable_optional_products(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.setenv("CENTRALMCP_TOOLSETS", "central,clearpass,apstra")
    backends = router._build_backends()
    assert "aruba-monitoring" in backends
    assert "aruba-glp" not in backends
    assert backends.get("clearpass-core") == "mcp_servers.clearpass"
    assert backends.get("apstra-core") == "mcp_servers.apstra"


def test_build_backends_toolsets_all_includes_known_optional(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.setenv("CENTRALMCP_TOOLSETS", "all")
    backends = router._build_backends()
    assert "aruba-config" in backends
    assert "clearpass-core" in backends
    assert "mist-core" in backends
    assert "apstra-core" in backends
    assert "aos8-core" in backends
    assert "edgeconnect-core" in backends


def test_find_tool_filters_semantic_hits_from_disabled_backends(monkeypatch):
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-rag": "mcp_servers.rag"})
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit: [])
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "create_vlan",
                "server": "aruba-config",
                "description": "disabled config tool",
                "schema_json": "{}",
                "score": 0.99,
            },
            {
                "name": "search_docs",
                "server": "aruba-rag",
                "description": "enabled rag tool",
                "schema_json": "{}",
                "score": 0.8,
            },
        ],
    )

    results = router.find_tool("vlan docs", top_k=5)

    assert [item["name"] for item in results] == ["search_docs"]


def test_default_router_exposes_ask_docs_wrapper_when_rag_enabled():
    assert "ask_docs" in router.mcp._tool_manager._tools


def test_invoke_tool_is_marked_destructive_because_it_can_dispatch_writes():
    annotations = router.mcp._tool_manager._tools["invoke_tool"].annotations

    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is True


def test_invoke_read_tool_is_marked_read_only():
    annotations = router.mcp._tool_manager._tools["invoke_read_tool"].annotations

    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False


def test_ask_docs_wrapper_forwards_backend_question_arg(monkeypatch):
    calls = []

    async def fake_invoke_tool(ctx, name, arguments=None):
        calls.append((ctx, name, arguments))
        return {"answer": "ok"}

    monkeypatch.setattr(router, "invoke_tool", fake_invoke_tool)

    result = asyncio.run(router.ask_docs(object(), "How do I configure WLANs?", top_k=2))

    assert result == {"answer": "ok"}
    assert calls == [
        (
            calls[0][0],
            "ask_docs",
            {"question": "How do I configure WLANs?", "top_k": 2},
        )
    ]


def test_find_device_wrapper_forwards_backend_serial_arg(monkeypatch):
    calls = []

    async def fake_invoke_tool(ctx, name, arguments=None):
        calls.append((ctx, name, arguments))
        return {"serialNumber": "AP1"}

    monkeypatch.setattr(router, "invoke_tool", fake_invoke_tool)

    result = asyncio.run(router.find_device(object(), "AP1"))

    assert result == {"serialNumber": "AP1"}
    assert calls == [(calls[0][0], "find_device", {"serial_number": "AP1"})]


def test_find_client_wrapper_forwards_backend_mac_arg(monkeypatch):
    calls = []

    async def fake_invoke_tool(ctx, name, arguments=None):
        calls.append((ctx, name, arguments))
        return {"macAddress": "aa:bb:cc:dd:ee:ff"}

    monkeypatch.setattr(router, "invoke_tool", fake_invoke_tool)

    result = asyncio.run(router.find_client(object(), "aa:bb:cc:dd:ee:ff"))

    assert result == {"macAddress": "aa:bb:cc:dd:ee:ff"}
    assert calls == [
        (calls[0][0], "find_client", {"mac_or_ip": "aa:bb:cc:dd:ee:ff"})
    ]
