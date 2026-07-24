"""MCP server — Aruba tool router (lazy loading via semantic tool RAG).

Supports three exposure modes:
  minimal  — find_tool + invoke_read_tool + invoke_tool only
  default  — minimal plus convenience wrappers
  direct   — default plus every enabled backend tool registered directly

Backend servers are imported in-process — no subprocess overhead.

Optional product backends can be enabled with:
  CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis

Toolsets can narrow loaded backends:
  CENTRALMCP_TOOLSETS=central,rag

Point MCP clients at THIS server instead of individual backend servers to keep
context cost low and let small local models pick tools reliably.
"""

import importlib
import json
import os
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from mcp_servers.prompts import register_router_prompts
from mcp_servers.shared import (
    DESTRUCTIVE,
    DIAGNOSTIC,
    PLATFORM_WRITE_GATE_NAMES,
    READ_ONLY,
    build_write_execution_contract,
    optional_product_access_mode,
    platform_write_blocked,
    platform_write_gate_state,
    platform_writes_allowed,
)

_BACKEND = os.getenv("CENTRALMCP_RAG_BACKEND", "lancedb").strip().lower()
_ROUTER_MODE = os.getenv("CENTRALMCP_ROUTER_MODE", "default").strip().lower()

if _BACKEND == "redis":
    from pipeline.clients.ollama_client import OllamaClient

    try:
        from pipeline.clients.redis_client import (
            TOOLS_INDEX,
        )
        from pipeline.clients.redis_client import (
            get_client as _get_redis,
        )
        from pipeline.clients.redis_client import (
            search_tools as _search_tools,
        )
        _redis_tools = _get_redis()
        _redis_tools.ping()
    except Exception:
        _redis_tools = None
    _ollama = OllamaClient()
else:
    from pipeline.clients import lance_client as _lance
    from pipeline.clients.embed_client import EmbedClient

    _embedder = EmbedClient()  # lazy — the ONNX model loads on first query

mcp = FastMCP("aruba-tool-router")
register_router_prompts(mcp)

# Backend MCP modules (loaded lazily on first invoke_tool).
_BACKENDS_BASE = {
    "aruba-config": "mcp_servers.config",
    "aruba-monitoring": "mcp_servers.monitoring",
    "aruba-nac": "mcp_servers.nac",
    "aruba-ops": "mcp_servers.ops",
    "aruba-glp": "mcp_servers.glp",
    "aruba-rag": "mcp_servers.rag",
}
_GENERATED_BACKENDS = {
    "aruba-central-generated": "mcp_servers.central_generated",
}
_OPTIONAL_BACKENDS = {
    "clearpass": ("clearpass-core", "mcp_servers.clearpass"),
    "mist": ("mist-core", "mcp_servers.mist"),
    "apstra": ("apstra-core", "mcp_servers.apstra"),
    "aos8": ("aos8-core", "mcp_servers.aos8"),
    "edgeconnect": ("edgeconnect-core", "mcp_servers.edgeconnect"),
    "uxi": ("uxi-core", "mcp_servers.uxi"),
    "axis": ("axis-core", "mcp_servers.axis"),
}
_OPTIONAL_SERVER_NAMES = {server_name for server_name, _ in _OPTIONAL_BACKENDS.values()}
_SERVER_PLATFORMS = {
    "aruba-config": "central",
    "aruba-monitoring": "central",
    "aruba-nac": "central",
    "aruba-ops": "central",
    "aruba-central-generated": "central",
    "aruba-glp": "glp",
    "aruba-rag": "rag",
    **{
        server_name: product
        for product, (server_name, _) in _OPTIONAL_BACKENDS.items()
    },
}
_TOOLSET_BACKENDS = {
    "config": {"aruba-config"},
    "monitoring": {"aruba-monitoring"},
    "nac": {"aruba-nac"},
    "ops": {"aruba-ops"},
    "glp": {"aruba-glp"},
    "rag": {"aruba-rag"},
    "central": {"aruba-config", "aruba-monitoring", "aruba-nac", "aruba-ops"},
    "central-generated": {"aruba-central-generated"},
    "clearpass": {"clearpass-core"},
    "mist": {"mist-core"},
    "apstra": {"apstra-core"},
    "aos8": {"aos8-core"},
    "edgeconnect": {"edgeconnect-core"},
    "uxi": {"uxi-core"},
    "axis": {"axis-core"},
}


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _product_access() -> str:
    return optional_product_access_mode()


def _optional_writes_allowed() -> bool:
    return _product_access() == "read-write"


def _is_read_only_tool(tool: Any) -> bool:
    return bool(getattr(getattr(tool, "annotations", None), "readOnlyHint", False))


def _is_diagnostic_tool(tool: Any) -> bool:
    return getattr(tool, "annotations", None) == DIAGNOSTIC


def _tool_capability(tool: Any) -> str:
    annotations = getattr(tool, "annotations", None)
    if bool(getattr(annotations, "readOnlyHint", False)):
        return "read"
    if bool(getattr(annotations, "destructiveHint", False)):
        return "destructive"
    if _is_diagnostic_tool(tool):
        return "diagnostic"
    return "write"


def _server_platform(server: str | None) -> str | None:
    if not server:
        return None
    return _SERVER_PLATFORMS.get(server, server.removesuffix("-core").removeprefix("aruba-"))


def _write_is_enabled(server: str | None, capability: str) -> bool:
    if capability not in {"write", "destructive"}:
        return True
    platform = _server_platform(server)
    if platform in PLATFORM_WRITE_GATE_NAMES:
        return platform_writes_allowed(platform)
    if server in _OPTIONAL_SERVER_NAMES:
        return _optional_writes_allowed()
    return True


def _schema_default(properties: dict[str, Any], name: str) -> Any:
    field = properties.get(name)
    return field.get("default") if isinstance(field, dict) else None


def _dry_run_state(
    properties: dict[str, Any],
    arguments: dict[str, Any] | None,
) -> str:
    if "dry_run" not in properties:
        return "unsupported"
    value = (
        arguments["dry_run"]
        if arguments is not None and "dry_run" in arguments
        else _schema_default(properties, "dry_run")
    )
    if arguments is None:
        if value is True:
            return "default_preview"
        if value is False:
            return "default_execution"
        return "unknown"
    if value is True:
        return "preview"
    if value is False:
        return "execution_requested"
    return "unknown"


def _contract_next_action(
    *,
    platform: str,
    capability: str,
    supports_dry_run: bool,
    dry_run_state: str,
    supports_confirm: bool,
    requires_confirmation: bool,
    arguments: dict[str, Any] | None,
    result: Any = None,
) -> str:
    gate = platform_write_gate_state(platform)
    if not gate["enabled"]:
        retry = (
            "call invoke_tool with dry_run=true to preview"
            if supports_dry_run
            else "retry invoke_tool after explicit user approval"
        )
        return f"Set {gate['env_var']}=1, then {retry}."

    if isinstance(result, dict):
        for key in ("next_step", "execute_hint"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if "error" in result:
            confirmed = bool((arguments or {}).get("confirm", False))
            if requires_confirmation and supports_confirm and not confirmed:
                return "Retry invoke_tool with confirm=true after explicit user approval."
            return "Correct the reported error, then retry invoke_tool."
        if dry_run_state == "preview":
            confirm_arg = " and confirm=true" if supports_confirm else ""
            return (
                "Review the preview, then call invoke_tool again with "
                f"dry_run=false{confirm_arg}."
            )
        return "No further safety action is required; review the backend result."

    if supports_dry_run:
        return "Call invoke_tool with dry_run=true to preview the change."
    if requires_confirmation and supports_confirm:
        return "Call invoke_tool with confirm=true after explicit user approval."
    if capability == "destructive":
        return (
            "Call invoke_tool after explicit user approval; the backend confirmation "
            "flow will run."
        )
    return "Call invoke_tool only after explicit user intent."


def _execution_contract(
    tool: Any,
    server: str | None,
    schema: dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
    result: Any = None,
) -> dict[str, Any] | None:
    capability = _tool_capability(tool)
    if capability not in {"write", "destructive"}:
        return None
    platform = _server_platform(server)
    if platform not in PLATFORM_WRITE_GATE_NAMES:
        return None
    properties = schema.get("properties") or {}
    supports_dry_run = "dry_run" in properties
    supports_confirm = "confirm" in properties
    requires_confirmation = capability == "destructive" or supports_confirm
    dry_run_state = _dry_run_state(properties, arguments)
    return build_write_execution_contract(
        platform,
        capability,
        supports_dry_run=supports_dry_run,
        dry_run_state=dry_run_state,
        supports_confirm=supports_confirm,
        requires_confirmation=requires_confirmation,
        idempotent=bool(
            getattr(getattr(tool, "annotations", None), "idempotentHint", False)
        ),
        next_action=_contract_next_action(
            platform=platform,
            capability=capability,
            supports_dry_run=supports_dry_run,
            dry_run_state=dry_run_state,
            supports_confirm=supports_confirm,
            requires_confirmation=requires_confirmation,
            arguments=arguments,
            result=result,
        ),
    )


def _discovery_metadata(tool: Any, server: str | None, schema: dict[str, Any]) -> dict[str, Any]:
    capability = _tool_capability(tool)
    properties = schema.get("properties") or {}
    supports_confirm = "confirm" in properties
    metadata = {
        "platform": _server_platform(server),
        "capability": capability,
        "recommended_dispatcher": (
            "invoke_read_tool" if capability == "read" else "invoke_tool"
        ),
        "requires_write_enablement": capability in {"write", "destructive"},
        "currently_enabled": bool(server in _BACKENDS)
        and _write_is_enabled(server, capability),
        "supports_dry_run": "dry_run" in properties,
        "supports_confirm": supports_confirm,
        "requires_confirmation": capability == "destructive"
        or (supports_confirm and capability == "write"),
        **_annotation_flags(tool),
    }
    contract = _execution_contract(tool, server, schema)
    if contract is not None:
        metadata["execution_contract"] = contract
    return metadata


def _matches_discovery_filters(
    item: dict[str, Any],
    *,
    platform: str | None,
    server: str | None,
    capability: str | None,
) -> bool:
    if platform and str(item.get("platform", "")).lower() != platform.strip().lower():
        return False
    if server and str(item.get("server", "")).lower() != server.strip().lower():
        return False
    if capability and item.get("capability") != capability:
        return False
    return True


def _optional_write_disabled(name: str, tool: Any | None = None, server: str | None = None) -> bool:
    tool = tool or _tool_index.get(name)
    server = server or _tool_backend_names.get(name)
    return (
        server in _OPTIONAL_SERVER_NAMES
        and tool is not None
        and _tool_capability(tool) in {"write", "destructive"}
        and not _write_is_enabled(server, _tool_capability(tool))
    )


def _build_backends() -> dict[str, str]:
    """Build backend module map, including optional product backends.

    Optional products/toolsets are enabled via:
      CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis
      CENTRALMCP_TOOLSETS=central,glp,rag
    Unknown product names are ignored.
    """
    products = _csv_env("CENTRALMCP_PRODUCTS")
    toolsets = _csv_env("CENTRALMCP_TOOLSETS")

    optional_by_server = {
        server_name: module_path
        for server_name, module_path in _OPTIONAL_BACKENDS.values()
    }
    all_backends = {**_BACKENDS_BASE, **_GENERATED_BACKENDS, **optional_by_server}

    if not toolsets:
        out = dict(_BACKENDS_BASE)
    elif "all" in toolsets:
        out = dict(all_backends)
    else:
        wanted_servers: set[str] = set()
        for toolset in toolsets:
            wanted_servers.update(_TOOLSET_BACKENDS.get(toolset, set()))
        out = {server: all_backends[server] for server in wanted_servers if server in all_backends}

    for product in products:
        spec = _OPTIONAL_BACKENDS.get(product)
        if spec:
            server_name, module_path = spec
            out[server_name] = module_path
    return out


_BACKENDS = _build_backends()
_tool_index: dict[str, Any] = {}  # name -> FastMCP Tool
_tool_servers: dict[str, Any] = {}  # name -> owning FastMCP backend (for dispatch)
_tool_backend_names: dict[str, str] = {}  # name -> owning server name


def _load_all_backends() -> None:
    """Import every backend once and index tools by name."""
    if _tool_index:
        return
    for server_name, module_path in _BACKENDS.items():
        mod = importlib.import_module(module_path)
        for name, tool in mod.mcp._tool_manager._tools.items():
            if _optional_write_disabled(name, tool, server_name):
                continue
            previous = _tool_backend_names.get(name)
            if previous is not None and previous != server_name:
                raise RuntimeError(
                    f"duplicate backend tool name {name!r}: {previous!r} and {server_name!r}"
                )
            _tool_index[name] = tool
            _tool_servers[name] = mod.mcp
            _tool_backend_names[name] = server_name


def _register_direct_backend_tools(target: FastMCP | None = None) -> list[str]:
    """Register every enabled backend tool directly on the router server."""
    target = target or mcp
    _load_all_backends()
    existing = set(target._tool_manager._tools)
    registered: list[str] = []
    for name, tool in _tool_index.items():
        if name in existing:
            # Router wrappers intentionally retain their compact forwarding
            # signatures when a backend exposes the same public tool name.
            continue
        target.add_tool(
            tool.fn,
            name=name,
            description=tool.description,
            annotations=tool.annotations,
        )
        existing.add(name)
        registered.append(name)
    return registered


# ── find_tool ────────────────────────────────────────────────────────────────

# Common verbs that also appear in tool names — don't let them dominate overlap.
_STOPWORDS = {"list", "get", "set", "find", "the", "a", "an", "of", "for", "to",
              "on", "at", "in", "and", "or", "all", "one", "new", "show", "view"}


def _keyword_hits(query: str, limit: int, include_schema: bool = False) -> list[dict]:
    """High-precision keyword fallback: require a *non-stopword* tool-name-token match.

    Guards against the model asking generic 'list APs' and getting every
    list_* tool ranked by coincidence. Only fires when the query mentions
    something specific like 'vlan', 'ssid', 'mac', 'firmware'.
    """
    _load_all_backends()
    q_tokens = {
        w for w in query.lower().replace("_", " ").split()
        if len(w) >= 3 and w not in _STOPWORDS
    }
    if not q_tokens:
        return []
    scored: list[tuple[float, Any]] = []
    for name, tool in _tool_index.items():
        if _optional_write_disabled(name, tool):
            continue
        name_tokens = set(name.lower().split("_")) - _STOPWORDS
        overlap = q_tokens & name_tokens
        if not overlap:
            continue
        # Score by how much of the tool name was matched (precision-oriented).
        score = len(overlap) / max(len(name_tokens), 1)
        scored.append((score, tool))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, t in scored[:limit]:
        schema = t.parameters if isinstance(t.parameters, dict) else {}
        item = {
            "name": t.name,
            "server": (server := _tool_backend_names.get(t.name)),
            "description": (t.description or "").strip(),
            "params": list((schema.get("properties") or {}).keys()),
            "score": round(score, 4),
            "match": "keyword",
            **_discovery_metadata(t, server, schema),
        }
        if include_schema:
            item["schema"] = schema
        out.append(item)
    return out


def _annotation_flags(tool: Any) -> dict[str, bool]:
    annotations = getattr(tool, "annotations", None)
    return {
        "read_only": bool(getattr(annotations, "readOnlyHint", False)),
        "destructive": bool(getattr(annotations, "destructiveHint", False)),
        "idempotent": bool(getattr(annotations, "idempotentHint", False)),
    }


@mcp.tool(annotations=READ_ONLY)
def find_tool(
    query: str,
    top_k: int = 5,
    include_schema: bool = False,
    platform: str | None = None,
    server: str | None = None,
    capability: Literal["read", "diagnostic", "write", "destructive"] | None = None,
) -> list[dict[str, Any]]:
    """Find tools by query. Combines semantic search + tool-name keyword match.

    Call this first when you need an action. The returned `name` is what you
    pass to invoke_read_tool for read-only tools or invoke_tool for writes.
    Results are deduplicated; semantic matches are annotated match='semantic',
    name-overlap matches match='keyword', and safety flags mirror backend
    ToolAnnotations. Results are compact by default; set include_schema=True
    only when you need the full JSON schema for a selected tool. Optional
    platform, server, and normalized capability filters apply to both keyword
    and semantic matches.

    Args:
        query: What you want to do. e.g. "create a VLAN", "disconnect a client".
        top_k: 1-10 results (default 5).
        include_schema: Include full JSON schemas in results. Defaults to False
            to keep MCP responses compact.
        platform: Filter by normalized platform, such as central, glp, mist,
            clearpass, or apstra.
        server: Filter by exact backend server name, such as aruba-monitoring.
        capability: Filter by read, diagnostic, write, or destructive.
    """
    top_k = max(1, min(top_k, 10))
    # Split the budget so one match type can't starve the other.
    kw_budget = max(1, top_k // 2)
    sem_budget = top_k - kw_budget
    by_name: dict[str, dict[str, Any]] = {}
    semantic_error: str | None = None

    keyword_candidates = _keyword_hits(
        query, min(max(top_k * 4, 20), 50), include_schema=include_schema
    )
    for h in keyword_candidates:
        if not _matches_discovery_filters(
            h, platform=platform, server=server, capability=capability
        ):
            continue
        by_name[h["name"]] = h
        if len(by_name) >= kw_budget:
            break

    try:
        if _BACKEND == "redis":
            hits = []
            if _redis_tools is not None:
                vec = _ollama.embed(query)
                hits = _search_tools(
                    _redis_tools,
                    vec,
                    top_k=min(max(top_k * 4, 20), 50),
                    index_name=TOOLS_INDEX,
                )
        else:
            vec = _embedder.embed_query(query)
            hits = _lance.search_tools(
                _lance.connect(), query, vec, top_k=min(max(top_k * 4, 20), 50)
            )
        added = 0
        for h in hits:
            name = h.get("name", "")
            hit_server = h.get("server")
            if not name or name in by_name or hit_server not in _BACKENDS:
                continue
            if name not in _tool_index:
                _load_all_backends()
            tool = _tool_index.get(name)
            if tool is None:
                continue
            if (
                hit_server in _OPTIONAL_SERVER_NAMES
                and _optional_write_disabled(name, tool, hit_server)
            ):
                continue
            indexed_schema = json.loads(h.get("schema_json") or "{}")
            published_schema = getattr(tool, "parameters", None)
            schema = (
                published_schema
                if isinstance(published_schema, dict)
                else indexed_schema
            )
            metadata = _discovery_metadata(tool, hit_server, schema)
            candidate = {
                "name": name,
                "server": hit_server,
                "description": h.get("description", ""),
                "params": list((schema.get("properties") or {}).keys()),
                "score": h.get("score", 0.0),
                "match": "semantic",
                **metadata,
            }
            if not _matches_discovery_filters(
                candidate,
                platform=platform,
                server=server,
                capability=capability,
            ):
                continue
            if added >= sem_budget + max(0, kw_budget - len(by_name)):
                break
            if include_schema:
                candidate["schema"] = schema
            by_name[name] = candidate
            added += 1
    except Exception as exc:
        semantic_error = f"{type(exc).__name__}: {exc}"

    if not by_name and semantic_error:
        return [
            {
                "error": f"Tool semantic search unavailable: {semantic_error}",
                "hint": "Rebuild the tool index with `uv run python scripts/ingest_tools.py`.",
            }
        ]
    return list(by_name.values())[:top_k]


# ── invoke_read_tool / invoke_tool ───────────────────────────────────────────

async def _dispatch_tool(ctx: Context, name: str, arguments: dict[str, Any] | None = None) -> Any:
    _load_all_backends()
    backend = _tool_servers.get(name)
    if backend is None:
        return {"error": f"Unknown tool '{name}'. Use find_tool to discover."}
    args = {k: v for k, v in (arguments or {}).items() if v is not None}
    tool = _tool_index[name]
    server = _tool_backend_names.get(name)
    schema = tool.parameters if isinstance(tool.parameters, dict) else {}
    capability = _tool_capability(tool)
    contract = _execution_contract(tool, server, schema, arguments=args)
    platform = _server_platform(server)
    if (
        capability in {"write", "destructive"}
        and platform in PLATFORM_WRITE_GATE_NAMES
        and not _write_is_enabled(server, capability)
    ):
        assert contract is not None
        return platform_write_blocked(
            platform,
            name,
            capability=capability,
            execution_contract=contract,
        )
    try:
        result = await backend._tool_manager.call_tool(name, args, context=ctx)
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    if contract is None:
        return result
    contract = _execution_contract(
        tool,
        server,
        schema,
        arguments=args,
        result=result,
    )
    if isinstance(result, dict):
        return {**result, "execution_contract": contract}
    return {"result": result, "execution_contract": contract}


@mcp.tool(annotations=READ_ONLY)
async def invoke_read_tool(
    ctx: Context,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Call a read-only Aruba tool by name (from find_tool).

    This refuses tools that are not annotated read-only. Use invoke_tool only
    for write/destructive tools after explicit user intent.
    """
    _load_all_backends()
    tool = _tool_index.get(name)
    if tool is None:
        return {"error": f"Unknown tool '{name}'. Use find_tool to discover."}
    if not bool(getattr(getattr(tool, "annotations", None), "readOnlyHint", False)):
        return {
            "error": (
                f"Tool '{name}' is not read-only. Use invoke_tool only after "
                "explicit user intent for write/destructive actions."
            ),
            "tool": name,
            "status": "blocked",
        }
    return await _dispatch_tool(ctx, name, arguments)


@mcp.tool(annotations=DESTRUCTIVE)
async def invoke_tool(
    ctx: Context,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Call an Aruba tool by name (from find_tool). Arguments is a kwargs dict.

    Example: invoke_tool("create_vlan", {"vlan_id": 200, "vlan_name": "Guest"})

    Dispatches through the owning backend's FastMCP tool manager, so arguments
    get FastMCP validation/coercion and the router's request Context is forwarded
    — this is what lets the async, ctx-requiring destructive ops tools
    (reboot_device/port_bounce/poe_bounce/disconnect_client) reach their
    confirmation elicitation. (FastMCP injects `ctx` here and strips it from the
    published schema, so callers only pass name + arguments.)
    """
    return await _dispatch_tool(ctx, name, arguments)


# ── Optional discovery convenience tools ──────────────────────────────────────
#
# default mode: include convenience wrappers (list_sites/find_device/etc.)
# minimal mode: expose only find_tool + invoke_read_tool + invoke_tool to minimize tool-list tokens
if _ROUTER_MODE != "minimal" and "aruba-monitoring" in _BACKENDS:
    @mcp.tool(annotations=READ_ONLY)
    async def list_scopes(
        ctx: Context, limit: int = 100, offset: int = 0, full_list: bool = False
    ) -> dict[str, Any]:
        """List Central scopes (sites, groups, global) — ID + name (paginated)."""
        return await invoke_tool(
            ctx, "list_scopes", {"limit": limit, "offset": offset, "full_list": full_list}
        )


    @mcp.tool(annotations=READ_ONLY)
    async def get_global_scope_id(ctx: Context) -> dict[str, Any]:
        """Return the global (org-wide) scope-id."""
        return await invoke_tool(ctx, "get_global_scope_id")


    @mcp.tool(annotations=READ_ONLY)
    async def list_sites(
        ctx: Context, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """List sites (paginated)."""
        return await invoke_tool(ctx, "list_sites", {"limit": limit, "offset": offset})


    @mcp.tool(annotations=READ_ONLY)
    async def list_devices(
        ctx: Context, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """List devices (paginated)."""
        return await invoke_tool(ctx, "list_devices", {"limit": limit, "offset": offset})


    @mcp.tool(annotations=READ_ONLY)
    async def find_device(ctx: Context, query: str) -> dict[str, Any]:
        """Find a device by serial number."""
        return await invoke_tool(ctx, "find_device", {"serial_number": query})


    @mcp.tool(annotations=READ_ONLY)
    async def find_client(ctx: Context, query: str) -> dict[str, Any]:
        """Find a client by name / MAC / IP."""
        return await invoke_tool(ctx, "find_client", {"mac_or_ip": query})


if _ROUTER_MODE != "minimal" and "aruba-rag" in _BACKENDS:
    @mcp.tool(annotations=READ_ONLY)
    async def ask_docs(ctx: Context, query: str, top_k: int = 5) -> Any:
        """Ask Aruba/HPE docs for a compact cited answer.

        Use this for prose/how-to questions when you want a short answer instead
        of raw retrieval hits. Exact endpoint/schema questions should still use
        lookup_api first.
        """
        return await invoke_tool(ctx, "ask_docs", {"question": query, "top_k": top_k})


    @mcp.tool(annotations=READ_ONLY)
    async def search_docs(
        ctx: Context,
        query: str,
        top_k: int = 5,
        source: str | None = None,
    ) -> Any:
        """Search Aruba/HPE documentation (Central config, APIs, NAC, VSG).

        For EXACT API questions (enum values, endpoints, schema fields) prefer
        lookup_api — it is lossless; this is fuzzy retrieval.
        """
        args: dict[str, Any] = {"query": query, "top_k": top_k}
        if source:
            args["source"] = source
        return await invoke_tool(ctx, "search_docs", args)


    @mcp.tool(annotations=READ_ONLY)
    async def lookup_api(ctx: Context, query: str, top_k: int = 10) -> Any:
        """Exact Aruba Central API lookup — endpoints, schemas, fields, enum values.

        Use INSTEAD of search_docs for "what enum values does field X accept",
        "which endpoint configures Y and with what method", or "what fields does
        schema Z have". Authoritative answers from the parsed OpenAPI specs.
        Returns [] when the specs hold no confident answer — fall back to
        search_docs in that case.
        """
        return await invoke_tool(ctx, "lookup_api", {"query": query, "top_k": top_k})


if _ROUTER_MODE == "direct":
    _register_direct_backend_tools()


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        MacNormalizeMiddleware,
        NullStripMiddleware,
        RateLimitMiddleware,
        ResponseEnvelopeMiddleware,
        SecretTokenizeMiddleware,
        UnknownToolSuggestMiddleware,
        install_middleware,
    )

    def _suggest_router_tool(name: str, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "description": item.get("description", ""),
                "match": item.get("match", "keyword"),
                "score": item.get("score", 0.0),
            }
            for item in _keyword_hits(name.replace("_", " "), limit)
        ]

    middlewares = [
        NullStripMiddleware(),
        RateLimitMiddleware(rate=8.0),
        UnknownToolSuggestMiddleware(
            lambda: mcp._tool_manager._tools,
            suggestion_provider=_suggest_router_tool,
        ),
        ResponseEnvelopeMiddleware(),
        SecretTokenizeMiddleware(),
    ]
    if os.getenv("CENTRALMCP_NORMALIZE_MACS", "").strip().lower() in {"1", "true", "yes"}:
        middlewares.append(MacNormalizeMiddleware())
    stable_list_tools(mcp)
    install_middleware(mcp, middlewares)
    from mcp_servers.shared import READ_ONLY, run_server
    run_server(mcp)
