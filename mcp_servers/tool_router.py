"""MCP server — Aruba tool router (lazy loading via semantic tool RAG).

Exposes a lean surface (find_tool + invoke_read_tool + invoke_tool + optional
discovery wrappers) and retrieves the full tool catalog on demand. Backend
servers are imported in-process and dispatched by name — no subprocess overhead.

Optional product backends can be enabled with:
  CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect

Toolsets can narrow loaded backends:
  CENTRALMCP_TOOLSETS=central,rag

Point MCP clients at THIS server instead of individual backend servers to keep
context cost low and let small local models pick tools reliably.
"""

import importlib
import json
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_servers.prompts import register_router_prompts
from mcp_servers.shared import DESTRUCTIVE, READ_ONLY

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
_OPTIONAL_BACKENDS = {
    "clearpass": ("clearpass-core", "mcp_servers.clearpass"),
    "mist": ("mist-core", "mcp_servers.mist"),
    "apstra": ("apstra-core", "mcp_servers.apstra"),
    "aos8": ("aos8-core", "mcp_servers.aos8"),
    "edgeconnect": ("edgeconnect-core", "mcp_servers.edgeconnect"),
}
_TOOLSET_BACKENDS = {
    "config": {"aruba-config"},
    "monitoring": {"aruba-monitoring"},
    "nac": {"aruba-nac"},
    "ops": {"aruba-ops"},
    "glp": {"aruba-glp"},
    "rag": {"aruba-rag"},
    "central": {"aruba-config", "aruba-monitoring", "aruba-nac", "aruba-ops"},
    "clearpass": {"clearpass-core"},
    "mist": {"mist-core"},
    "apstra": {"apstra-core"},
    "aos8": {"aos8-core"},
    "edgeconnect": {"edgeconnect-core"},
}


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _build_backends() -> dict[str, str]:
    """Build backend module map, including optional product backends.

    Optional products/toolsets are enabled via:
      CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect
      CENTRALMCP_TOOLSETS=central,glp,rag
    Unknown product names are ignored.
    """
    products = _csv_env("CENTRALMCP_PRODUCTS")
    toolsets = _csv_env("CENTRALMCP_TOOLSETS")

    optional_by_server = {
        server_name: module_path
        for server_name, module_path in _OPTIONAL_BACKENDS.values()
    }
    all_backends = {**_BACKENDS_BASE, **optional_by_server}

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
            _tool_index[name] = tool
            _tool_servers[name] = mod.mcp
            _tool_backend_names[name] = server_name


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
            "server": _tool_backend_names.get(t.name),
            "description": (t.description or "").strip(),
            "params": list((schema.get("properties") or {}).keys()),
            "score": round(score, 4),
            "match": "keyword",
            **_annotation_flags(t),
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
def find_tool(query: str, top_k: int = 5, include_schema: bool = False) -> list[dict[str, Any]]:
    """Find tools by query. Combines semantic search + tool-name keyword match.

    Call this first when you need an action. The returned `name` is what you
    pass to invoke_read_tool for read-only tools or invoke_tool for writes.
    Results are deduplicated; semantic matches are annotated match='semantic',
    name-overlap matches match='keyword', and safety flags mirror backend
    ToolAnnotations. Results are compact by default; set include_schema=True
    only when you need the full JSON schema for a selected tool.

    Args:
        query: What you want to do. e.g. "create a VLAN", "disconnect a client".
        top_k: 1-10 results (default 5).
        include_schema: Include full JSON schemas in results. Defaults to False
            to keep MCP responses compact.
    """
    top_k = max(1, min(top_k, 10))
    # Split the budget so one match type can't starve the other.
    kw_budget = max(1, top_k // 2)
    sem_budget = top_k - kw_budget
    by_name: dict[str, dict[str, Any]] = {}
    semantic_error: str | None = None

    for h in _keyword_hits(query, kw_budget, include_schema=include_schema):
        by_name[h["name"]] = h

    try:
        if _BACKEND == "redis":
            hits = []
            if _redis_tools is not None:
                vec = _ollama.embed(query)
                hits = _search_tools(_redis_tools, vec, top_k=top_k * 2, index_name=TOOLS_INDEX)
        else:
            vec = _embedder.embed_query(query)
            hits = _lance.search_tools(_lance.connect(), query, vec, top_k=top_k * 2)
        added = 0
        for h in hits:
            name = h.get("name", "")
            server = h.get("server")
            if not name or name in by_name or server not in _BACKENDS:
                continue
            if added >= sem_budget + max(0, kw_budget - len(by_name)):
                break
            schema = json.loads(h.get("schema_json") or "{}")
            item = {
                "name": name,
                "server": server,
                "description": h.get("description", ""),
                "params": list((schema.get("properties") or {}).keys()),
                "score": h.get("score", 0.0),
                "match": "semantic",
                **_annotation_flags(_tool_index.get(name)),
            }
            if include_schema:
                item["schema"] = schema
            by_name[name] = item
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
    try:
        return await backend._tool_manager.call_tool(name, args, context=ctx)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


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
    async def list_scopes(ctx: Context) -> dict[str, Any]:
        """List Central scopes (sites, groups, global) — ID + name."""
        return await invoke_tool(ctx, "list_scopes")


    @mcp.tool(annotations=READ_ONLY)
    async def get_global_scope_id(ctx: Context) -> dict[str, Any]:
        """Return the global (org-wide) scope-id."""
        return await invoke_tool(ctx, "get_global_scope_id")


    @mcp.tool(annotations=READ_ONLY)
    async def list_sites(
        ctx: Context, limit: int = 50, offset: int = 0, full_list: bool = False
    ) -> dict[str, Any]:
        """List sites (paginated)."""
        return await invoke_tool(
            ctx, "list_sites", {"limit": limit, "offset": offset, "full_list": full_list}
        )


    @mcp.tool(annotations=READ_ONLY)
    async def list_devices(
        ctx: Context, limit: int = 50, offset: int = 0, full_list: bool = False
    ) -> dict[str, Any]:
        """List devices (paginated)."""
        return await invoke_tool(
            ctx, "list_devices", {"limit": limit, "offset": offset, "full_list": full_list}
        )


    @mcp.tool(annotations=READ_ONLY)
    async def find_device(ctx: Context, query: str) -> dict[str, Any]:
        """Find a device by name / serial / MAC / IP."""
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


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        MacNormalizeMiddleware,
        NullStripMiddleware,
        RateLimitMiddleware,
        ResponseEnvelopeMiddleware,
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
    ]
    if os.getenv("CENTRALMCP_NORMALIZE_MACS", "").strip().lower() in {"1", "true", "yes"}:
        middlewares.append(MacNormalizeMiddleware())
    stable_list_tools(mcp)
    install_middleware(mcp, middlewares)
    from mcp_servers.shared import READ_ONLY, run_server
    run_server(mcp)
