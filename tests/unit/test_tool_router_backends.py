from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_servers.tool_router as router
from mcp_servers.shared import IDEMPOTENT_WRITE

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    monkeypatch.setenv(
        "CENTRALMCP_PRODUCTS", "clearpass,mist,apstra,aos8,edgeconnect,uxi,axis"
    )
    backends = router._build_backends()
    assert backends.get("clearpass-core") == "mcp_servers.clearpass"
    assert backends.get("mist-core") == "mcp_servers.mist"
    assert backends.get("apstra-core") == "mcp_servers.apstra"
    assert backends.get("aos8-core") == "mcp_servers.aos8"
    assert backends.get("edgeconnect-core") == "mcp_servers.edgeconnect"
    assert backends.get("uxi-core") == "mcp_servers.uxi"
    assert backends.get("axis-core") == "mcp_servers.axis"


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
    assert "uxi-core" in backends
    assert "axis-core" in backends


def test_load_all_backends_keeps_diagnostics_in_read_only_mode(monkeypatch):
    backend = FastMCP("diagnostic-backend")

    @backend.tool(annotations=router.DIAGNOSTIC)
    def run_diagnostic() -> dict:
        return {"ok": True}

    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    monkeypatch.setattr(router, "_BACKENDS", {"edgeconnect-core": "demo.diagnostic"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})
    monkeypatch.setattr(
        router.importlib,
        "import_module",
        lambda path: SimpleNamespace(mcp=backend),
    )

    router._load_all_backends()

    assert "run_diagnostic" in router._tool_index


def test_load_all_backends_filters_optional_writes_when_read_only(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    monkeypatch.setattr(router, "_BACKENDS", {"clearpass-core": "mcp_servers.clearpass"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})

    router._load_all_backends()

    assert "clearpass_status" in router._tool_index
    assert "clearpass_get" in router._tool_index
    assert "clearpass_write" not in router._tool_index
    assert "clearpass_delete_guest" not in router._tool_index


def test_load_all_backends_exposes_optional_writes_when_read_write(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-write")
    monkeypatch.setattr(router, "_BACKENDS", {"clearpass-core": "mcp_servers.clearpass"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})

    router._load_all_backends()

    assert "clearpass_status" in router._tool_index
    assert "clearpass_write" in router._tool_index
    assert "clearpass_delete_guest" in router._tool_index


@pytest.mark.parametrize(
    ("shared_access", "axis_override", "expected"),
    [
        ("read-only", "1", True),
        ("read-write", "0", False),
        ("read-write", "invalid", False),
    ],
)
def test_load_all_backends_honors_platform_override_precedence(
    monkeypatch,
    shared_access,
    axis_override,
    expected,
):
    backend = FastMCP("axis-override")

    @backend.tool(annotations=IDEMPOTENT_WRITE)
    def axis_update_widget() -> dict:
        return {"updated": True}

    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", shared_access)
    monkeypatch.setenv("CENTRALMCP_AXIS_WRITES", axis_override)
    monkeypatch.setattr(router, "_BACKENDS", {"axis-core": "demo.axis"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})
    monkeypatch.setattr(
        router.importlib,
        "import_module",
        lambda path: SimpleNamespace(mcp=backend),
    )

    router._load_all_backends()

    assert ("axis_update_widget" in router._tool_index) is expected


def test_load_all_backends_rejects_cross_backend_name_collisions(monkeypatch):
    first = FastMCP("first")
    second = FastMCP("second")

    @first.tool()
    def duplicate_name() -> str:
        return "first"

    @second.tool()
    def duplicate_name() -> str:  # noqa: F811
        return "second"

    modules = {
        "demo.first": SimpleNamespace(mcp=first),
        "demo.second": SimpleNamespace(mcp=second),
    }
    monkeypatch.setattr(router, "_BACKENDS", {"first": "demo.first", "second": "demo.second"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})
    monkeypatch.setattr(router.importlib, "import_module", modules.__getitem__)

    with pytest.raises(RuntimeError, match="duplicate backend tool name"):
        router._load_all_backends()


def test_direct_mode_registers_enabled_backend_tools(monkeypatch):
    backend = FastMCP("backend")
    target = FastMCP("router-direct")

    @backend.tool()
    def direct_example(value: str) -> str:
        return value

    monkeypatch.setattr(router, "_BACKENDS", {"demo": "demo.backend"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_tool_servers", {})
    monkeypatch.setattr(router, "_tool_backend_names", {})
    monkeypatch.setattr(
        router.importlib,
        "import_module",
        lambda module_path: SimpleNamespace(mcp=backend),
    )

    registered = router._register_direct_backend_tools(target)

    assert registered == ["direct_example"]
    assert "direct_example" in target._tool_manager._tools


def test_public_docs_list_router_products_and_toolsets():
    readme = (REPO_ROOT / "README.md").read_text()
    getting_started = (REPO_ROOT / "docs" / "getting-started.md").read_text()
    tool_router = (REPO_ROOT / "docs" / "tool-router.md").read_text()
    optional_products = ",".join(router._OPTIONAL_BACKENDS)

    assert f"CENTRALMCP_PRODUCTS={optional_products}" in readme
    assert f"CENTRALMCP_PRODUCTS={optional_products}" in getting_started
    assert f"CENTRALMCP_PRODUCTS={optional_products}" in tool_router

    for toolset in {*router._TOOLSET_BACKENDS, "all"}:
        assert f"`{toolset}`" in tool_router

    for text in (readme, tool_router):
        assert "`include_schema=true`" in text


def test_find_tool_filters_semantic_hits_from_disabled_backends(monkeypatch):
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-rag": "mcp_servers.rag"})
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
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


def test_find_tool_filters_optional_write_hits_when_read_only(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"clearpass-core": "mcp_servers.clearpass"})
    monkeypatch.setattr(
        router,
        "_tool_index",
        {
            "clearpass_write": SimpleNamespace(
                annotations=SimpleNamespace(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                )
            ),
            "clearpass_status": SimpleNamespace(
                annotations=SimpleNamespace(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                )
            ),
        },
    )
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {
            "clearpass_write": "clearpass-core",
            "clearpass_status": "clearpass-core",
        },
    )
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "clearpass_write",
                "server": "clearpass-core",
                "description": "write tool",
                "schema_json": "{}",
                "score": 0.99,
            },
            {
                "name": "clearpass_status",
                "server": "clearpass-core",
                "description": "status tool",
                "schema_json": "{}",
                "score": 0.8,
            },
        ],
    )

    results = router.find_tool("clearpass write status", top_k=5)

    assert [item["name"] for item in results] == ["clearpass_status"]


def test_find_tool_omits_schema_by_default(monkeypatch):
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-config": "mcp_servers.config"})
    monkeypatch.setattr(
        router,
        "_tool_index",
        {
            "create_vlan": SimpleNamespace(
                annotations=IDEMPOTENT_WRITE,
                parameters={"properties": {"vlan_id": {"type": "integer"}}},
            )
        },
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "create_vlan",
                "server": "aruba-config",
                "description": "Create a VLAN",
                "schema_json": '{"properties": {"vlan_id": {"type": "integer"}}}',
                "score": 0.9,
            }
        ],
    )

    result = router.find_tool("create vlan", top_k=1)

    assert result[0]["params"] == ["vlan_id"]
    assert "schema" not in result[0]


def test_find_tool_can_include_schema_when_requested(monkeypatch):
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-config": "mcp_servers.config"})
    monkeypatch.setattr(
        router,
        "_tool_index",
        {
            "create_vlan": SimpleNamespace(
                annotations=IDEMPOTENT_WRITE,
                parameters={"properties": {"vlan_id": {"type": "integer"}}},
            )
        },
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "create_vlan",
                "server": "aruba-config",
                "description": "Create a VLAN",
                "schema_json": '{"properties": {"vlan_id": {"type": "integer"}}}',
                "score": 0.9,
            }
        ],
    )

    result = router.find_tool("create vlan", top_k=1, include_schema=True)

    assert result[0]["schema"] == {"properties": {"vlan_id": {"type": "integer"}}}


def test_find_tool_hydrates_annotations_for_semantic_only_results(monkeypatch):
    def load_tools():
        router._tool_index["search_docs"] = SimpleNamespace(
            annotations=SimpleNamespace(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
            )
        )

    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-rag": "mcp_servers.rag"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router, "_load_all_backends", load_tools)
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "search_docs",
                "server": "aruba-rag",
                "description": "Search docs",
                "schema_json": "{}",
                "score": 0.9,
            }
        ],
    )

    result = router.find_tool("documentation help", top_k=1)

    assert result[0]["read_only"] is True
    assert result[0]["destructive"] is False
    assert result[0]["idempotent"] is True


@pytest.mark.parametrize(
    ("annotations", "schema", "capability", "dispatcher", "confirmation_required"),
    [
        (router.READ_ONLY, {}, "read", "invoke_read_tool", False),
        (router.DIAGNOSTIC, {}, "diagnostic", "invoke_tool", False),
        (
            IDEMPOTENT_WRITE,
            {"properties": {"confirm": {"type": "boolean"}}},
            "write",
            "invoke_tool",
            True,
        ),
        (router.DESTRUCTIVE, {}, "destructive", "invoke_tool", True),
    ],
)
def test_discovery_capability_is_normalized_from_annotations(
    monkeypatch,
    annotations,
    schema,
    capability,
    dispatcher,
    confirmation_required,
):
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-config": "demo.config"})
    metadata = router._discovery_metadata(
        SimpleNamespace(annotations=annotations),
        "aruba-config",
        schema,
    )

    assert metadata["capability"] == capability
    assert metadata["recommended_dispatcher"] == dispatcher
    assert metadata["requires_confirmation"] is confirmation_required


def test_find_tool_filters_keyword_results_and_reports_write_contract(monkeypatch):
    backend = FastMCP("discovery-keyword")

    @backend.tool(annotations=router.READ_ONLY)
    def list_widgets(limit: int = 10) -> dict:
        return {"limit": limit}

    @backend.tool(annotations=IDEMPOTENT_WRITE)
    def update_widget(
        widget_id: str,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        return {"widget_id": widget_id, "dry_run": dry_run, "confirm": confirm}

    tools = dict(backend._tool_manager._tools)
    monkeypatch.setenv("CENTRALMCP_CENTRAL_WRITES", "0")
    monkeypatch.setattr(
        router,
        "_BACKENDS",
        {
            "aruba-monitoring": "demo.monitoring",
            "aruba-config": "demo.config",
        },
    )
    monkeypatch.setattr(router, "_tool_index", tools)
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {
            "list_widgets": "aruba-monitoring",
            "update_widget": "aruba-config",
        },
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [],
    )

    result = router.find_tool(
        "widget",
        top_k=5,
        platform="central",
        server="aruba-config",
        capability="write",
    )

    assert [item["name"] for item in result] == ["update_widget"]
    item = result[0]
    assert item["platform"] == "central"
    assert item["capability"] == "write"
    assert item["recommended_dispatcher"] == "invoke_tool"
    assert item["requires_write_enablement"] is True
    assert item["currently_enabled"] is False
    assert item["supports_dry_run"] is True
    assert item["supports_confirm"] is True
    assert item["requires_confirmation"] is True
    assert item["read_only"] is False
    assert item["destructive"] is False
    assert item["idempotent"] is True
    assert item["origin"] == "curated"
    assert item["execution_contract"] == {
        "platform": "central",
        "capability": "write",
        "gate": {
            "env_var": "CENTRALMCP_CENTRAL_WRITES",
            "state": "disabled",
            "source": "platform_override",
        },
        "dry_run": {"supported": True, "state": "default_preview"},
        "confirm": {"supported": True, "required": True},
        "idempotent": True,
        "next_action": (
            "Set CENTRALMCP_CENTRAL_WRITES=1, then call invoke_tool with "
            "dry_run=true to preview."
        ),
    }


def test_find_tool_filters_generated_origin_and_operation_id(monkeypatch):
    backend = FastMCP("discovery-generated")

    @backend.tool(annotations=router.READ_ONLY)
    def generated_widget(widget_id: str) -> dict:
        return {"widget_id": widget_id}

    tools = dict(backend._tool_manager._tools)
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-monitoring": "demo.monitoring"})
    monkeypatch.setattr(router, "_tool_index", tools)
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {"generated_widget": "aruba-monitoring"},
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    monkeypatch.setattr(
        router,
        "_generated_tool_records",
        {
            "generated_widget": {
                "operation_id": "getGeneratedWidget",
                "operation_key": "GET /widgets/{widget_id}",
                "manifest_platform": "central",
            }
        },
    )
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(router._lance, "search_tools", lambda *args, **kwargs: [])

    result = router.find_tool(
        "generated widget",
        origin="generated",
        operation_id="getGeneratedWidget",
    )

    assert [item["name"] for item in result] == ["generated_widget"]
    assert result[0]["origin"] == "generated"
    assert result[0]["operation_id"] == "getGeneratedWidget"
    assert result[0]["operation_key"] == "GET /widgets/{widget_id}"

    assert router.find_tool("generated widget", origin="curated") == []


def test_find_tool_filters_semantic_results_by_diagnostic_capability(monkeypatch):
    backend = FastMCP("discovery-semantic")

    @backend.tool(annotations=router.READ_ONLY)
    def mist_widget_status() -> dict:
        return {"status": "ok"}

    @backend.tool(annotations=router.DIAGNOSTIC)
    def mist_widget_diagnostic() -> dict:
        return {"status": "healthy"}

    tools = dict(backend._tool_manager._tools)
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"mist-core": "demo.mist"})
    monkeypatch.setattr(router, "_tool_index", tools)
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {name: "mist-core" for name in tools},
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    monkeypatch.setattr(
        router,
        "_keyword_hits",
        lambda query, limit, include_schema=False: [],
    )
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [
            {
                "name": "mist_widget_status",
                "server": "mist-core",
                "description": "Read widget status",
                "schema_json": "{}",
                "score": 0.99,
            },
            {
                "name": "mist_widget_diagnostic",
                "server": "mist-core",
                "description": "Run widget diagnostic",
                "schema_json": "{}",
                "score": 0.9,
            },
        ],
    )

    result = router.find_tool(
        "widget health",
        top_k=2,
        platform="mist",
        server="mist-core",
        capability="diagnostic",
    )

    assert [item["name"] for item in result] == ["mist_widget_diagnostic"]
    item = result[0]
    assert item["capability"] == "diagnostic"
    assert item["recommended_dispatcher"] == "invoke_tool"
    assert item["requires_write_enablement"] is False
    assert item["currently_enabled"] is True
    assert item["supports_dry_run"] is False
    assert item["supports_confirm"] is False
    assert item["requires_confirmation"] is False
    assert item["read_only"] is False
    assert item["destructive"] is False
    assert item["idempotent"] is False
    assert "execution_contract" not in item


def _configure_dispatch_backend(monkeypatch, *, annotation=IDEMPOTENT_WRITE):
    backend = FastMCP("router-dispatch")
    calls: list[dict] = []

    @backend.tool(annotations=annotation)
    def update_widget(
        widget_id: str,
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict:
        calls.append(
            {"widget_id": widget_id, "dry_run": dry_run, "confirm": confirm}
        )
        if dry_run:
            return {"dry_run": True, "widget_id": widget_id}
        if not confirm:
            return {"error": "confirm=True is required."}
        return {"updated": widget_id}

    tool = backend._tool_manager._tools["update_widget"]
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-config": "demo.config"})
    monkeypatch.setattr(router, "_tool_index", {"update_widget": tool})
    monkeypatch.setattr(router, "_tool_servers", {"update_widget": backend})
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {"update_widget": "aruba-config"},
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)
    return calls


def test_router_dispatch_adds_contract_to_dry_run_preview(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_CENTRAL_WRITES", "1")
    calls = _configure_dispatch_backend(monkeypatch)

    result = asyncio.run(
        router._dispatch_tool(object(), "update_widget", {"widget_id": "w1"})
    )

    assert result["dry_run"] is True
    assert result["widget_id"] == "w1"
    assert calls == [{"widget_id": "w1", "dry_run": True, "confirm": False}]
    contract = result["execution_contract"]
    assert contract["dry_run"]["state"] == "preview"
    assert contract["confirm"] == {"supported": True, "required": True}
    assert contract["idempotent"] is True
    assert contract["next_action"] == (
        "Review the preview, then call invoke_tool again with "
        "dry_run=false and confirm=true."
    )


def test_router_dispatch_blocks_invalid_gate_without_calling_backend(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_CENTRAL_WRITES", "invalid")
    calls = _configure_dispatch_backend(monkeypatch)

    result = asyncio.run(
        router._dispatch_tool(
            object(),
            "update_widget",
            {"widget_id": "w1", "dry_run": False, "confirm": True},
        )
    )

    assert calls == []
    assert result["status"] == "blocked"
    assert result["execution_contract"]["gate"]["state"] == "invalid"
    assert result["execution_contract"]["dry_run"]["state"] == "execution_requested"
    assert result["execution_contract"]["next_action"].startswith(
        "Set CENTRALMCP_CENTRAL_WRITES=1"
    )


def test_router_dispatch_preserves_execution_result_and_contract(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_CENTRAL_WRITES", "1")
    _configure_dispatch_backend(monkeypatch)

    result = asyncio.run(
        router._dispatch_tool(
            object(),
            "update_widget",
            {"widget_id": "w1", "dry_run": False, "confirm": True},
        )
    )

    assert result["updated"] == "w1"
    assert result["execution_contract"]["dry_run"]["state"] == "execution_requested"
    assert result["execution_contract"]["next_action"].startswith(
        "No further safety action"
    )


def test_router_dispatch_preserves_fastmcp_validation(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_CENTRAL_WRITES", "1")
    calls = _configure_dispatch_backend(monkeypatch)

    result = asyncio.run(router._dispatch_tool(object(), "update_widget", {}))

    assert calls == []
    assert "validation" in result["error"].lower()
    assert result["execution_contract"]["capability"] == "write"


def test_router_dispatch_does_not_wrap_reads_or_diagnostics(monkeypatch):
    backend = FastMCP("router-non-write")

    @backend.tool(annotations=router.READ_ONLY)
    def read_widget() -> dict:
        return {"kind": "read"}

    @backend.tool(annotations=router.DIAGNOSTIC)
    def diagnose_widget() -> dict:
        return {"kind": "diagnostic"}

    tools = dict(backend._tool_manager._tools)
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-ops": "demo.ops"})
    monkeypatch.setattr(router, "_tool_index", tools)
    monkeypatch.setattr(router, "_tool_servers", {name: backend for name in tools})
    monkeypatch.setattr(
        router,
        "_tool_backend_names",
        {name: "aruba-ops" for name in tools},
    )
    monkeypatch.setattr(router, "_load_all_backends", lambda: None)

    read_result = asyncio.run(router._dispatch_tool(object(), "read_widget"))
    diagnostic_result = asyncio.run(
        router._dispatch_tool(object(), "diagnose_widget")
    )

    assert read_result == {"kind": "read"}
    assert diagnostic_result == {"kind": "diagnostic"}


def test_find_tool_reports_semantic_error_without_keyword_fallback(monkeypatch):
    def raise_index_missing(query):
        raise RuntimeError("index missing")

    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-config": "mcp_servers.config"})
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router._embedder, "embed_query", raise_index_missing)

    result = router.find_tool("create vlan", top_k=1)

    assert result == [
        {
            "error": "Tool semantic search unavailable: RuntimeError: index missing",
            "hint": "Rebuild the tool index with `uv run python scripts/ingest_tools.py`.",
        }
    ]


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
