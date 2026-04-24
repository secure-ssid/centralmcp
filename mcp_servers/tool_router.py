"""MCP server — Aruba tool router (lazy loading via semantic tool RAG).

Exposes a lean surface (find_tool + invoke_tool + common discovery tools)
and retrieves the full 145-tool catalog from Qdrant on demand. Backend
servers (config/monitoring/nac/ops/glp/rag) are imported in-process and
dispatched by name — no subprocess overhead.

Point MCP clients at THIS server instead of the 6 individual ones to cut
context cost ~80% and let small local models pick tools reliably.
"""

import asyncio
import importlib
import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from pipeline.clients.ollama_client import OllamaClient

try:
    from qdrant_client import QdrantClient as _QdrantClient
    from pipeline.clients.qdrant_client import QDRANT_URL
    _qdrant = _QdrantClient(url=QDRANT_URL)
except Exception:
    _qdrant = None

TOOLS_COLLECTION = "aruba_tools"

mcp = FastMCP("aruba-tool-router")
_ollama = OllamaClient()

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


def _load_all_backends() -> None:
    """Import every backend once and index tools by name."""
    if _tool_index:
        return
    for module_path in _BACKENDS.values():
        mod = importlib.import_module(module_path)
        for name, tool in mod.mcp._tool_manager._tools.items():
            _tool_index[name] = tool


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


@mcp.tool()
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

    if _qdrant is not None:
        try:
            vec = _ollama.embed(query)
            hits = _qdrant.query_points(
                collection_name=TOOLS_COLLECTION, query=vec, limit=top_k * 2,
            ).points
            added = 0
            for h in hits:
                name = h.payload["name"]
                if name in by_name or added >= sem_budget + (kw_budget - len(by_name)):
                    if name in by_name:
                        continue
                    break
                by_name[name] = {
                    "name": name,
                    "server": h.payload.get("server"),
                    "description": h.payload["description"],
                    "params": h.payload.get("params", []),
                    "schema": json.loads(h.payload.get("schema") or "{}"),
                    "score": round(h.score, 4),
                    "match": "semantic",
                }
                added += 1
        except Exception as exc:
            return list(by_name.values()) or [{"error": f"Qdrant: {exc}"}]

    return list(by_name.values())[:top_k]


# ── invoke_tool ──────────────────────────────────────────────────────────────

@mcp.tool()
def invoke_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an Aruba tool by name (from find_tool). Arguments is a kwargs dict.

    Example: invoke_tool("create_vlan", {"vlan_id": 200, "vlan_name": "Guest"})
    """
    _load_all_backends()
    tool = _tool_index.get(name)
    if tool is None:
        return {"error": f"Unknown tool '{name}'. Use find_tool to discover."}
    args = arguments or {}
    try:
        result = tool.fn(**args)
        if inspect.iscoroutine(result):
            result = asyncio.get_event_loop().run_until_complete(result)
        return result
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Always-available discovery tools (used in nearly every session) ──────────

@mcp.tool()
def list_scopes() -> dict[str, Any]:
    """List Central scopes (sites, groups, global) — ID + name."""
    return invoke_tool("list_scopes")


@mcp.tool()
def get_global_scope_id() -> dict[str, Any]:
    """Return the global (org-wide) scope-id."""
    return invoke_tool("get_global_scope_id")


@mcp.tool()
def list_sites(limit: int = 50, offset: int = 0, full_list: bool = False) -> dict[str, Any]:
    """List sites (paginated)."""
    return invoke_tool("list_sites", {"limit": limit, "offset": offset, "full_list": full_list})


@mcp.tool()
def list_devices(limit: int = 50, offset: int = 0, full_list: bool = False) -> dict[str, Any]:
    """List devices (paginated)."""
    return invoke_tool("list_devices", {"limit": limit, "offset": offset, "full_list": full_list})


@mcp.tool()
def find_device(query: str) -> dict[str, Any]:
    """Find a device by name / serial / MAC / IP."""
    return invoke_tool("find_device", {"query": query})


@mcp.tool()
def find_client(query: str) -> dict[str, Any]:
    """Find a client by name / MAC / IP."""
    return invoke_tool("find_client", {"query": query})


@mcp.tool()
def search_docs(query: str, top_k: int = 5, source: str | None = None) -> Any:
    """Search Aruba/HPE documentation (Central config, APIs, NAC, VSG)."""
    args: dict[str, Any] = {"query": query, "top_k": top_k}
    if source:
        args["source"] = source
    return invoke_tool("search_docs", args)


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    mcp.run()
