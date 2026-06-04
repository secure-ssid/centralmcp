"""MCP server — Aruba tool router (lazy loading via semantic tool RAG).

Exposes a lean surface (find_tool + invoke_tool + common discovery tools)
and retrieves the full 149-tool catalog on demand. Backend servers
(config/monitoring/nac/ops/glp/rag) are imported in-process and dispatched
by name — no subprocess overhead.

Point MCP clients at THIS server instead of the 6 individual ones to cut
context cost ~80% and let small local models pick tools reliably.
"""

import importlib
import json
import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_servers.shared import READ_ONLY

_BACKEND = os.getenv("CENTRALMCP_RAG_BACKEND", "lancedb").strip().lower()

if _BACKEND == "redis":
    from pipeline.clients.ollama_client import OllamaClient

    try:
        from pipeline.clients.redis_client import TOOLS_INDEX, get_client as _get_redis
        from pipeline.clients.redis_client import search_tools as _search_tools
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

# Backend MCP modules (loaded lazily on first invoke_tool).
_BACKENDS = {
    "aruba-config": "mcp_servers.config",
    "aruba-monitoring": "mcp_servers.monitoring",
    "aruba-nac": "mcp_servers.nac",
    "aruba-ops": "mcp_servers.ops",
    "aruba-glp": "mcp_servers.glp",
    "aruba-rag": "mcp_servers.rag",
}
_tool_index: dict[str, Any] = {}  # name -> FastMCP Tool
_tool_servers: dict[str, Any] = {}  # name -> owning FastMCP backend (for dispatch)


def _load_all_backends() -> None:
    """Import every backend once and index tools by name."""
    if _tool_index:
        return
    for module_path in _BACKENDS.values():
        mod = importlib.import_module(module_path)
        for name, tool in mod.mcp._tool_manager._tools.items():
            _tool_index[name] = tool
            _tool_servers[name] = mod.mcp


# ── find_tool ────────────────────────────────────────────────────────────────

# Common verbs that also appear in tool names — don't let them dominate overlap.
_STOPWORDS = {"list", "get", "set", "find", "the", "a", "an", "of", "for", "to",
              "on", "at", "in", "and", "or", "all", "one", "new", "show", "view"}


def _keyword_hits(query: str, limit: int) -> list[dict]:
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
        out.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "params": list((schema.get("properties") or {}).keys()),
            "schema": schema,
            "score": round(score, 4),
            "match": "keyword",
        })
    return out


@mcp.tool(annotations=READ_ONLY)
def find_tool(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Find Aruba tools by query. Combines semantic search + tool-name keyword match.

    Call this first when you need an action. The returned `name` is what you
    pass to invoke_tool. Results are deduplicated; semantic matches are
    annotated match='semantic', name-overlap matches match='keyword'.

    Args:
        query: What you want to do. e.g. "create a VLAN", "disconnect a client".
        top_k: 1-10 results (default 5).
    """
    top_k = max(1, min(top_k, 10))
    # Split the budget so one match type can't starve the other.
    kw_budget = max(1, top_k // 2)
    sem_budget = top_k - kw_budget
    by_name: dict[str, dict[str, Any]] = {}

    for h in _keyword_hits(query, kw_budget):
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
            if not name or name in by_name:
                continue
            if added >= sem_budget + max(0, kw_budget - len(by_name)):
                break
            by_name[name] = {
                "name": name,
                "server": h.get("server"),
                "description": h.get("description", ""),
                "params": [],
                "schema": json.loads(h.get("schema_json") or "{}"),
                "score": h.get("score", 0.0),
                "match": "semantic",
            }
            added += 1
    except Exception:
        pass  # fall back to keyword-only results

    return list(by_name.values())[:top_k]


# ── invoke_tool ──────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
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
    _load_all_backends()
    backend = _tool_servers.get(name)
    if backend is None:
        return {"error": f"Unknown tool '{name}'. Use find_tool to discover."}
    args = arguments or {}
    try:
        return await backend._tool_manager.call_tool(name, args, context=ctx)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Always-available discovery tools (used in nearly every session) ──────────

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
    return await invoke_tool(ctx, "find_device", {"query": query})


@mcp.tool(annotations=READ_ONLY)
async def find_client(ctx: Context, query: str) -> dict[str, Any]:
    """Find a client by name / MAC / IP."""
    return await invoke_tool(ctx, "find_client", {"query": query})


@mcp.tool(annotations=READ_ONLY)
async def search_docs(ctx: Context, query: str, top_k: int = 5, source: str | None = None) -> Any:
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
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    from mcp_servers.shared import READ_ONLY, run_server
    run_server(mcp)
