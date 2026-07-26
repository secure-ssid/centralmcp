"""MCP server — Aruba tool router (lazy loading via semantic tool RAG).

Supports three exposure modes:
  minimal  — find_tool + invoke_read_tool + invoke_tool only
  default  — minimal plus convenience wrappers, including the read-only
             automation planners plan_tool_workflow and
             plan_reconciliation_schedule
  direct   — default plus every enabled backend tool registered directly

Backend servers are imported in-process — no subprocess overhead.

Optional product backends can be enabled with:
  CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis

Toolsets can narrow loaded backends:
  CENTRALMCP_TOOLSETS=central,rag

Point MCP clients at THIS server instead of individual backend servers to keep
context cost low and let small local models pick tools reliably.

v0.7 router automation: invoke_tool/invoke_read_tool dispatch enforces a
configurable, deterministic response item/byte budget (see
_bound_router_response / CENTRALMCP_ROUTER_RESPONSE_MAX_ITEMS /
CENTRALMCP_ROUTER_RESPONSE_MAX_BYTES) and plan_tool_workflow /
plan_reconciliation_schedule provide read-only, catalog-backed dependency
ordering and recurring-reconciliation planning (pipeline/router_automation.py)
without ever executing a tool.

invoke_read_tool also accepts an optional opaque `cursor` to resume a
previously truncated response (see "Continuation cursors" below). Cursors
are process-local (an HMAC key generated fresh at import time; a server
restart invalidates every outstanding cursor with an explicit error),
integrity-protected, bounded in length/TTL, and bound to the exact tool
name + canonical arguments that issued them. Only capability `read` tools
ever emit or accept a cursor; the generic destructive invoke_tool never
gains continuation support.
"""

import base64
import hashlib
import hmac
import importlib
import json
import os
import secrets
import time
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from mcp_servers.prompts import register_router_prompts
from mcp_servers.shared import (
    DESTRUCTIVE,
    DIAGNOSTIC,
    MAX_LIST_LIMIT,
    PLATFORM_WRITE_GATE_NAMES,
    READ_ONLY,
    bound_collection_response,
    build_write_execution_contract,
    optional_product_access_mode,
    platform_write_blocked,
    platform_write_gate_state,
    platform_writes_allowed,
)
from pipeline import artifact_contracts as _artifact_contracts
from pipeline import router_automation as _router_automation

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
    generated = _generated_records().get(str(getattr(tool, "name", "")))
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
        "origin": "generated" if generated is not None else "curated",
        **_annotation_flags(tool),
    }
    if generated is not None:
        metadata.update(
            {
                key: value
                for key, value in generated.items()
                if value is not None
            }
        )
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
    origin: str | None,
    operation_id: str | None,
) -> bool:
    if platform and str(item.get("platform", "")).lower() != platform.strip().lower():
        return False
    if server and str(item.get("server", "")).lower() != server.strip().lower():
        return False
    if capability and item.get("capability") != capability:
        return False
    if origin and item.get("origin") != origin:
        return False
    if operation_id and str(item.get("operation_id", "")).lower() != operation_id.lower():
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
_generated_tool_records: dict[str, dict[str, Any]] | None = None


def _generated_records() -> dict[str, dict[str, Any]]:
    """Map generated tool names to stable manifest provenance."""
    global _generated_tool_records
    if _generated_tool_records is not None:
        return _generated_tool_records
    from mcp_servers.openapi_gen.manifest import MANIFEST_DIR

    records: dict[str, dict[str, Any]] = {}
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for operation in manifest.get("operations") or []:
            if not isinstance(operation, dict) or not operation.get("name"):
                continue
            records[str(operation["name"])] = {
                "operation_id": operation.get("operation_id"),
                "operation_key": operation.get("key"),
                "manifest_platform": path.stem,
            }
    _generated_tool_records = records
    return records


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
    origin: Literal["curated", "generated"] | None = None,
    operation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Find tools by query. Combines semantic search + tool-name keyword match.

    Call this first when you need an action. The returned `name` is what you
    pass to invoke_read_tool for read-only tools or invoke_tool for writes.
    Results are deduplicated; semantic matches are annotated match='semantic',
    name-overlap matches match='keyword', and safety flags mirror backend
    ToolAnnotations. Results are compact by default; set include_schema=True
    only when you need the full JSON schema for a selected tool. Optional
    platform, server, normalized capability, curated/generated origin, and
    exact OpenAPI operation-ID filters apply to both keyword and semantic
    matches.

    Args:
        query: What you want to do. e.g. "create a VLAN", "disconnect a client".
        top_k: 1-10 results (default 5).
        include_schema: Include full JSON schemas in results. Defaults to False
            to keep MCP responses compact.
        platform: Filter by normalized platform, such as central, glp, mist,
            clearpass, or apstra.
        server: Filter by exact backend server name, such as aruba-monitoring.
        capability: Filter by read, diagnostic, write, or destructive.
        origin: Filter by curated or generated implementation.
        operation_id: Filter by an exact generated OpenAPI operationId.
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
            h,
            platform=platform,
            server=server,
            capability=capability,
            origin=origin,
            operation_id=operation_id,
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
                origin=origin,
                operation_id=operation_id,
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


# ── Response budgets / continuation metadata ─────────────────────────────────
#
# A configurable, deterministic safety net applied to every dispatched
# backend result (invoke_tool / invoke_read_tool only -- find_tool's own
# results are already bounded by top_k). Most curated tools already bound
# their own output (limit/offset, bound_collection_response); this exists
# for the remaining/optional/generated tools that don't, and to guarantee a
# hard ceiling regardless of backend behavior. A response already within
# budget is returned byte-for-byte unchanged -- no new keys are added --
# so this is invisible to existing callers/tests until a response actually
# needs clipping.

_RESPONSE_BUDGET_ITEMS_ENV = "CENTRALMCP_ROUTER_RESPONSE_MAX_ITEMS"
_RESPONSE_BUDGET_BYTES_ENV = "CENTRALMCP_ROUTER_RESPONSE_MAX_BYTES"
_RESPONSE_BUDGET_DEFAULT_ITEMS = MAX_LIST_LIMIT
_RESPONSE_BUDGET_DEFAULT_BYTES = 200_000
_RESPONSE_BUDGET_MIN_BYTES = 1024
_RESPONSE_BUDGET_MIN_ITEMS = 1
_RESPONSE_BUDGET_SHRINK_STEPS = 6


def _env_positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _response_budget_items() -> int:
    return min(
        _env_positive_int(
            _RESPONSE_BUDGET_ITEMS_ENV,
            _RESPONSE_BUDGET_DEFAULT_ITEMS,
        ),
        MAX_LIST_LIMIT,
    )


def _response_budget_bytes() -> int:
    return _env_positive_int(
        _RESPONSE_BUDGET_BYTES_ENV,
        _RESPONSE_BUDGET_DEFAULT_BYTES,
        minimum=_RESPONSE_BUDGET_MIN_BYTES,
    )


def _json_byte_size(value: Any) -> int | None:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return None


def _dict_primary_list_len(data: dict[str, Any]) -> tuple[str | None, int]:
    """Mirror bound_collection_response's own list-key selection so the
    "does this need bounding" pre-check never disagrees with the bounding
    it then applies."""
    candidates = [
        (key, len(value))
        for key, value in data.items()
        if key != "_pagination" and isinstance(value, list)
    ]
    if not candidates:
        return None, 0
    key, length = max(candidates, key=lambda kv: (kv[1], kv[0]))
    return key, length


def _response_bounds_marker(
    *, reason: str, item_limit: int | None, byte_limit: int, size_bytes: int | None = None
) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "truncated": True,
        "reason": reason,
        "byte_limit": byte_limit,
    }
    if item_limit is not None:
        marker["item_limit"] = item_limit
    if size_bytes is not None:
        marker["size_bytes"] = size_bytes
    return marker


# ── Continuation cursors (invoke_read_tool only) ─────────────────────────────
#
# An opaque, integrity-protected token that lets invoke_read_tool resume a
# previously truncated response. It is intentionally stateless server-side:
# it carries only {version, expiry, next-offset, tool-name digest,
# arguments digest}, never raw arguments/identifiers/results/credentials.
# Resuming simply re-dispatches the SAME tool + arguments and re-slices the
# fresh result starting at the stored offset -- safe only for capability
# "read" tools, which is enforced both here and by invoke_read_tool's own
# read-only gate (defense in depth). The signing key is a random value
# generated once per process; a restart silently invalidates every
# outstanding cursor (signature verification fails), which this module
# reports as an explicit, safe error rather than ever calling the backend.

_CURSOR_VERSION = 1
_CURSOR_TTL_ENV = "CENTRALMCP_ROUTER_CURSOR_TTL_SECONDS"
_CURSOR_DEFAULT_TTL_SECONDS = 900
_CURSOR_MIN_TTL_SECONDS = 30
_CURSOR_MAX_TTL_SECONDS = 3600
_CURSOR_MAX_LENGTH = 512
_CURSOR_DIGEST_HEX_CHARS = 16  # 64-bit truncated SHA-256; the HMAC (not this
# digest) is the anti-forgery boundary, so this only needs to be
# practically collision-free for distinct tool/argument combinations.
_CURSOR_MAC_BYTES = 16  # 128-bit truncated HMAC-SHA256; keeps cursors compact
# while remaining infeasible to forge without the process-local secret key.
_CURSOR_HMAC_KEY = secrets.token_bytes(32)


class CursorError(Exception):
    """Raised for any malformed/tampered/expired/mismatched cursor.

    Always caught before a backend call is made -- the message is safe to
    return directly to the caller (never includes raw arguments/secrets)."""


def _cursor_ttl_seconds() -> int:
    raw = _env_positive_int(_CURSOR_TTL_ENV, _CURSOR_DEFAULT_TTL_SECONDS, minimum=1)
    return max(_CURSOR_MIN_TTL_SECONDS, min(raw, _CURSOR_MAX_TTL_SECONDS))


def _strip_null_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (arguments or {}).items() if v is not None}


def _cursor_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_CURSOR_DIGEST_HEX_CHARS]


def _cursor_args_digest(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return _cursor_digest(canonical)


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_cursor_payload(payload_bytes: bytes) -> bytes:
    return hmac.new(_CURSOR_HMAC_KEY, payload_bytes, hashlib.sha256).digest()[:_CURSOR_MAC_BYTES]


def _encode_continuation_cursor(*, name: str, arguments: dict[str, Any], next_offset: int) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "exp": int(time.time()) + _cursor_ttl_seconds(),
        "off": int(next_offset),
        "t": _cursor_digest(name),
        "a": _cursor_args_digest(arguments),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _sign_cursor_payload(payload_bytes)
    return f"{_b64u_encode(payload_bytes)}.{_b64u_encode(signature)}"


def _decode_and_verify_continuation_cursor(
    cursor: str, *, name: str, arguments: dict[str, Any]
) -> int:
    """Validate ``cursor`` against ``name``/``arguments`` and return the
    resume offset. Raises :class:`CursorError` (never touches the backend)
    for anything malformed, tampered, expired, or bound to a different
    tool/arguments -- including a signature mismatch caused by a server
    restart (a fresh random key invalidates every prior cursor)."""
    if not isinstance(cursor, str) or not cursor:
        raise CursorError("cursor is missing or malformed")
    if len(cursor) > _CURSOR_MAX_LENGTH:
        raise CursorError("cursor exceeds the maximum allowed length")
    parts = cursor.split(".")
    if len(parts) != 2:
        raise CursorError("cursor is malformed")
    payload_b64, signature_b64 = parts
    try:
        payload_bytes = _b64u_decode(payload_b64)
        signature_bytes = _b64u_decode(signature_b64)
    except Exception as exc:
        raise CursorError("cursor is malformed") from exc
    expected_signature = _sign_cursor_payload(payload_bytes)
    if not hmac.compare_digest(signature_bytes, expected_signature):
        raise CursorError(
            "cursor signature is invalid (tampered, or the server process restarted)"
        )
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise CursorError("cursor is malformed") from exc
    if not isinstance(payload, dict):
        raise CursorError("cursor is malformed")
    if payload.get("v") != _CURSOR_VERSION:
        raise CursorError("cursor version is unsupported")
    expiry = payload.get("exp")
    if not isinstance(expiry, int) or isinstance(expiry, bool):
        raise CursorError("cursor is malformed")
    if int(time.time()) >= expiry:
        raise CursorError("cursor has expired")
    offset = payload.get("off")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise CursorError("cursor is malformed")
    if payload.get("t") != _cursor_digest(name):
        raise CursorError("cursor does not match the requested tool")
    if payload.get("a") != _cursor_args_digest(arguments):
        raise CursorError("cursor does not match the requested arguments")
    return offset


def _bound_router_response(
    result: Any,
    *,
    max_items: int | None = None,
    max_bytes: int | None = None,
    offset: int = 0,
    enable_cursor: bool = False,
    tool_name: str | None = None,
    tool_arguments: dict[str, Any] | None = None,
) -> Any:
    """Deterministically bound one dispatched tool result to a configurable
    item-count/byte-size budget, adding a stable ``_response_bounds``
    continuation marker only when clipping actually happened, plus an
    opaque MCP-style ``next_cursor`` when the caller is eligible to resume
    (``enable_cursor`` -- only ever set True by invoke_read_tool for
    capability ``read`` tools; invoke_tool never sets it).

    Never touches a non-dict/non-list scalar, and never touches a dict
    that already looks like an error response (an ``error`` key present)
    -- error/blocked payload shapes are preserved exactly. Reuses
    ``mcp_servers.shared.bound_collection_response`` for item-count
    slicing (the same ``_pagination`` shape already recognized by the
    audit/metrics truncation detectors) before falling back to a bounded
    text preview for content with nothing sliceable (mirroring
    ``mcp_servers.shared.bounded_response_payload``'s raw-body fallback).
    A single item too large to fit the byte budget is reported as
    explicitly non-resumable rather than emitting a cursor that would loop
    forever on the same oversized item.
    """
    if isinstance(result, dict) and "error" in result:
        return result
    if not isinstance(result, (dict, list)):
        return result

    requested_items_budget = (
        max_items if max_items is not None else _response_budget_items()
    )
    items_budget = max(1, min(requested_items_budget, MAX_LIST_LIMIT))
    bytes_budget = max_bytes if max_bytes is not None else _response_budget_bytes()
    resume_offset = max(0, int(offset or 0))

    if isinstance(result, list):
        primary_key, item_count = None, len(result)
        nothing_sliceable = False
    else:
        primary_key, item_count = _dict_primary_list_len(result)
        nothing_sliceable = primary_key is None

    if nothing_sliceable:
        # A stale/mismatched resume offset against a shape with nothing
        # sliceable is ignored defensively rather than corrupting output.
        resume_offset = 0

    size = _json_byte_size(result)
    remaining_count = max(0, item_count - resume_offset)
    item_overflow = remaining_count > items_budget
    byte_overflow = (
        resume_offset == 0
        and size is not None
        and size > bytes_budget
    )
    needs_paging = item_overflow or byte_overflow or resume_offset > 0
    if not needs_paging:
        return result

    if nothing_sliceable:
        # Nothing sliceable (byte overflow from scalar/nested-object bloat,
        # never from a bounded list) -- bounded text preview. Never
        # resumable: there is no list to page through.
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        raw = encoded.encode("utf-8")
        marker = _response_bounds_marker(
            reason="byte_budget", item_limit=None, byte_limit=bytes_budget, size_bytes=len(raw)
        )
        marker["resumable"] = False
        marker["resumable_reason"] = "no_sliceable_collection"
        return {"_response_bounds": marker, "preview": raw[:bytes_budget].decode(
            "utf-8", errors="replace"
        )}

    limit = items_budget
    page = bound_collection_response(result, limit=limit, offset=resume_offset)
    encoded_size = _json_byte_size(page)
    byte_shrunk = False
    for _ in range(_RESPONSE_BUDGET_SHRINK_STEPS):
        if encoded_size is not None and encoded_size <= bytes_budget:
            break
        if limit <= _RESPONSE_BUDGET_MIN_ITEMS:
            break
        limit = max(_RESPONSE_BUDGET_MIN_ITEMS, limit // 2)
        byte_shrunk = True
        page = bound_collection_response(result, limit=limit, offset=resume_offset)
        encoded_size = _json_byte_size(page)

    if encoded_size is not None and encoded_size > bytes_budget:
        # Even a single item (limit already at the floor) can't fit the
        # byte budget -- fall back to a bounded text preview instead of
        # returning an over-budget payload, and explicitly mark this
        # non-resumable so a caller never loops forever on the same item.
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        raw = encoded.encode("utf-8")
        marker = _response_bounds_marker(
            reason="byte_budget", item_limit=limit, byte_limit=bytes_budget, size_bytes=len(raw)
        )
        marker["resumable"] = False
        marker["resumable_reason"] = "single_item_exceeds_byte_budget"
        return {"_response_bounds": marker, "preview": raw[:bytes_budget].decode(
            "utf-8", errors="replace"
        )}

    slice_key = "items" if isinstance(result, list) else primary_key
    actual_count = (
        len(page.get(slice_key, [])) if isinstance(page, dict) and slice_key else 0
    )
    next_offset = resume_offset + actual_count
    pagination = page.get("_pagination") if isinstance(page, dict) else None
    truncated_by_items = bool(pagination and pagination.get("truncated"))
    was_clipped = truncated_by_items or byte_shrunk
    if not was_clipped:
        # A cursor-resume request that landed exactly on the final,
        # complete tail: still paginated (see _pagination), but nothing
        # was clipped relative to budget, so no _response_bounds/cursor.
        return page

    reasons = [
        reason
        for reason, present in (
            ("item_budget", item_overflow),
            ("byte_budget", byte_shrunk),
        )
        if present
    ] or ["item_budget"]
    can_emit_cursor = (
        enable_cursor
        and truncated_by_items
        and tool_name is not None
        and tool_arguments is not None
    )
    marker = _response_bounds_marker(
        reason="+".join(reasons), item_limit=limit, byte_limit=bytes_budget
    )
    marker["resumable"] = bool(can_emit_cursor)
    if isinstance(page, dict):
        page = {**page, "_response_bounds": marker}
        if can_emit_cursor:
            page["next_cursor"] = _encode_continuation_cursor(
                name=tool_name, arguments=tool_arguments, next_offset=next_offset
            )
            page["cursor_expires_in_seconds"] = _cursor_ttl_seconds()
    return page


# ── invoke_read_tool / invoke_tool ───────────────────────────────────────────

async def _dispatch_tool(
    ctx: Context,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    resume_offset: int = 0,
    enable_cursor: bool = False,
) -> Any:
    _load_all_backends()
    backend = _tool_servers.get(name)
    if backend is None:
        return {"error": f"Unknown tool '{name}'. Use find_tool to discover."}
    args = _strip_null_arguments(arguments)
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
    # Cursors are only ever eligible for capability "read" tools; this is a
    # redundant, defense-in-depth check -- enable_cursor is only ever passed
    # True by invoke_read_tool, which already refuses non-read-only tools.
    cursor_eligible = enable_cursor and capability == "read"
    result = _bound_router_response(
        result,
        offset=resume_offset,
        enable_cursor=cursor_eligible,
        tool_name=name,
        tool_arguments=args,
    )
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
    cursor: str | None = None,
) -> Any:
    """Call a read-only Aruba tool by name (from find_tool).

    This refuses tools that are not annotated read-only. Use invoke_tool only
    for write/destructive tools after explicit user intent.

    Args:
        cursor: Opaque ``next_cursor`` value from a previous truncated
            response, to resume it from where it left off. Only ever
            returned by this tool for capability "read" tools -- it is
            process-local (invalidated by a server restart), integrity
            protected, time-limited, and bound to this exact tool name and
            these exact arguments. A malformed/tampered/expired/mismatched
            cursor returns an error and never reaches the backend.
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
    resume_offset = 0
    if cursor is not None:
        canonical_args = _strip_null_arguments(arguments)
        try:
            resume_offset = _decode_and_verify_continuation_cursor(
                cursor, name=name, arguments=canonical_args
            )
        except CursorError as exc:
            return {"error": str(exc), "tool": name, "status": "invalid_cursor"}
    return await _dispatch_tool(
        ctx, name, arguments, resume_offset=resume_offset, enable_cursor=True
    )


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


# ── Router automation: dependency planning + reconciliation scheduling ──────
#
# Both tools below are strictly read-only/plan-only: they resolve tool
# references against the already-loaded, enabled backend catalog (never
# inferring an unavailable tool), and never call invoke_tool/invoke_read_tool
# themselves. Excluded from `minimal` mode to keep that profile's tool-list
# token cost at exactly find_tool + invoke_read_tool + invoke_tool.
if _ROUTER_MODE != "minimal":
    _PLAN_AMBIGUITY_MARGIN = 0.15

    def _plan_step_metadata(name: str) -> dict[str, Any]:
        tool = _tool_index[name]
        server = _tool_backend_names.get(name)
        capability = _tool_capability(tool)
        return {
            "server": server,
            "platform": _server_platform(server),
            "capability": capability,
            "recommended_dispatcher": (
                "invoke_read_tool" if capability == "read" else "invoke_tool"
            ),
        }

    def _resolve_plan_step_tool(
        step: dict[str, Any],
    ) -> tuple[str | None, bool, bool, list[dict[str, Any]]]:
        """Resolve one plan step to a catalog tool name.

        Returns ``(tool_name_or_None, resolved, ambiguous, candidates)``. An
        explicit ``"tool"`` name is resolved only against the currently
        loaded catalog (``_tool_index``) -- never guessed; an unknown name
        resolves to ``(None, False, False, [])``. A ``"hint"`` falls back to
        the same bounded, deterministic keyword search ``find_tool`` uses
        (no semantic/embedding call), and is marked ambiguous when more than
        one candidate scores within ``_PLAN_AMBIGUITY_MARGIN`` of the top
        score.
        """
        explicit = step.get("tool")
        if explicit:
            name = str(explicit)
            if name not in _tool_index:
                return None, False, False, []
            return name, True, False, [{"name": name, **_plan_step_metadata(name)}]
        hint = step.get("hint")
        if not hint:
            return None, False, False, []
        candidates = _keyword_hits(str(hint), _router_automation.MAX_PLAN_CANDIDATES_PER_STEP)
        if not candidates:
            return None, False, False, []
        top_score = candidates[0].get("score", 0.0)
        close = [
            c for c in candidates if top_score - c.get("score", 0.0) <= _PLAN_AMBIGUITY_MARGIN
        ]
        ambiguous = len(close) > 1
        return candidates[0]["name"], True, ambiguous, candidates

    @mcp.tool(annotations=READ_ONLY)
    def plan_tool_workflow(
        steps: list[dict[str, Any]],
        include_candidates: bool = False,
    ) -> dict[str, Any]:
        """Build a deterministic, read-only dependency/order plan across enabled backend tools.

        Never executes any tool. Every resolved tool reference is checked only
        against the currently loaded, enabled backend catalog (the same index
        find_tool searches) -- an unresolved or ambiguous reference is
        reported explicitly, never guessed or silently dropped.

        Args:
            steps: bounded (max 25) list of step specs. Each step is a dict:
                - "id": optional stable step id (str); defaults to "step_<index>".
                - "tool": exact tool name to resolve via the loaded catalog
                  (preferred -- deterministic, exact match, never guessed).
                - "hint": free-text action description used only when "tool"
                  is omitted; resolved via the same bounded keyword search
                  find_tool uses (no semantic/embedding guessing). Marked
                  "ambiguous" when multiple close-scoring candidates exist.
                - "depends_on": list of step ids (or exact tool names) that
                  must run before this step.
            include_candidates: include up to 5 scored candidate tools per
                unresolved/ambiguous step. Defaults to False to keep the plan
                compact.

        Returns "ok", "steps" (resolved metadata per step), "order"
        (topological order, or None whenever any step/dependency is
        unresolved or the graph has a cycle), "acyclic", "cycles",
        "unresolved_step_ids", "unresolved_dependencies", and "artifact" (a
        router_dependency_plan-shaped payload suitable for
        pipeline.artifact_contracts.write_artifact -- never written to disk
        by this tool). This never calls invoke_tool/invoke_read_tool.
        """
        _load_all_backends()
        if not isinstance(steps, list) or not steps:
            return {"ok": False, "error": "steps must be a non-empty list", "steps": []}
        if len(steps) > _router_automation.MAX_PLAN_STEPS:
            return {
                "ok": False,
                "error": (
                    f"steps has {len(steps)} entries, exceeding the "
                    f"{_router_automation.MAX_PLAN_STEPS} bound"
                ),
                "steps": [],
            }

        step_ids: list[str] = []
        by_tool_name: dict[str, str] = {}
        resolved_steps: list[dict[str, Any]] = []
        errors: list[str] = []

        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                errors.append(f"steps[{index}] must be an object")
                step_id = f"step_{index}"
                step_ids.append(step_id)
                resolved_steps.append(
                    {
                        "id": step_id,
                        "tool": None,
                        "resolved": False,
                        "ambiguous": False,
                        "server": None,
                        "platform": None,
                        "capability": "unknown",
                        "recommended_dispatcher": None,
                        "depends_on": [],
                    }
                )
                continue
            step_id = str(raw_step.get("id") or f"step_{index}")
            if step_id in step_ids:
                errors.append(f"duplicate step id: {step_id!r}")
            step_ids.append(step_id)
            tool_name, resolved, ambiguous, candidates = _resolve_plan_step_tool(raw_step)
            entry: dict[str, Any] = {
                "id": step_id,
                "tool": tool_name,
                "resolved": resolved,
                "ambiguous": ambiguous,
                "depends_on": [str(d) for d in (raw_step.get("depends_on") or [])],
            }
            if resolved and tool_name is not None:
                entry.update(_plan_step_metadata(tool_name))
                by_tool_name[tool_name] = step_id
            else:
                entry.update(
                    {
                        "server": None,
                        "platform": None,
                        "capability": "unknown",
                        "recommended_dispatcher": None,
                    }
                )
            if include_candidates and (not resolved or ambiguous):
                entry["candidates"] = [
                    {"name": c["name"], "server": c.get("server"), "score": c.get("score")}
                    for c in candidates[: _router_automation.MAX_PLAN_CANDIDATES_PER_STEP]
                ]
            resolved_steps.append(entry)

        # depends_on may reference a step id OR a resolved tool name.
        edges: dict[str, list[str]] = {}
        unresolved_dependencies: list[dict[str, str]] = []
        for entry in resolved_steps:
            deps: list[str] = []
            for dep in entry["depends_on"]:
                if dep in step_ids:
                    deps.append(dep)
                elif dep in by_tool_name:
                    deps.append(by_tool_name[dep])
                else:
                    unresolved_dependencies.append({"step": entry["id"], "missing": dep})
            edges[entry["id"]] = deps

        order, cycles = _router_automation.resolve_dependency_order(step_ids, edges)
        acyclic = not cycles
        unresolved_step_ids = [e["id"] for e in resolved_steps if not e["resolved"]]
        blocked = (
            bool(errors)
            or bool(unresolved_dependencies)
            or bool(unresolved_step_ids)
            or not acyclic
        )
        effective_order = order if not blocked else None

        artifact: dict[str, Any] | None = None
        artifact_error: str | None = None
        try:
            artifact_steps = [
                {
                    "step_id": entry["id"],
                    "tool": entry["tool"],
                    "resolved": entry["resolved"],
                    "ambiguous": entry["ambiguous"],
                    "capability": entry["capability"],
                    "platform": entry["platform"],
                    "depends_on": entry["depends_on"],
                }
                for entry in resolved_steps
            ]
            payload = _router_automation.build_dependency_plan_payload(
                steps=artifact_steps,
                order=effective_order,
                acyclic=acyclic,
                cycles=cycles,
                unresolved_step_ids=unresolved_step_ids,
            )
            built = _artifact_contracts.build_artifact(
                _artifact_contracts.ROUTER_DEPENDENCY_PLAN, payload
            )
            artifact = _artifact_contracts.to_json_dict(built)
        except _artifact_contracts.ArtifactValidationError as exc:
            artifact_error = str(exc)

        return {
            "ok": not errors and not blocked,
            "steps": resolved_steps,
            "order": effective_order,
            "acyclic": acyclic,
            "cycles": cycles,
            "unresolved_step_ids": unresolved_step_ids,
            "unresolved_dependencies": unresolved_dependencies,
            "errors": errors,
            "artifact": artifact,
            "artifact_error": artifact_error,
        }

    @mcp.tool(annotations=READ_ONLY)
    def plan_reconciliation_schedule(
        cadence: dict[str, Any] | str,
        tools: list[str] | None = None,
        platforms: list[str] | None = None,
        servers: list[str] | None = None,
        max_entries: int = 50,
    ) -> dict[str, Any]:
        """Build a bounded, read-only, plan-only recurring reconciliation schedule.

        Never creates an OS timer, cron job, or GitHub Actions schedule, and
        never executes a tool -- this only validates a cadence and resolves a
        bounded set of currently enabled tools into a schedule
        *specification*. Write/destructive tools are always excluded from the
        executable entry list (reported in "excluded" instead, with a
        reason), regardless of whether the caller explicitly requested them.

        Args:
            cadence: either a named cadence string ("hourly", "daily",
                "weekly") or an object such as
                {"kind": "interval_minutes", "interval_minutes": 30} or
                {"kind": "cron", "expression": "*/15 * * * *"}. Validated
                structurally only -- never parsed into an actual next-run
                time or registered as a real schedule.
            tools: exact tool names to resolve via the loaded catalog. Omit
                to fall back to the platforms/servers filters below.
            platforms: normalized platform filter (e.g. "central", "glp")
                applied to the loaded catalog when tools is omitted.
            servers: exact backend server name filter (e.g.
                "aruba-monitoring") applied to the loaded catalog when tools
                is omitted.
            max_entries: safety ceiling on schedule entries (default 50, max
                100).

        Returns "ok", "cadence" (validated descriptor), "entries"
        (read/diagnostic tools only), "excluded" (everything else, with a
        reason), "dry_run" (always True), and "artifact" (a
        router_reconciliation_plan-shaped payload suitable for
        pipeline.artifact_contracts.write_artifact -- never written to disk
        by this tool).
        """
        _load_all_backends()
        cadence_result = _router_automation.validate_cadence(cadence)
        if not cadence_result.get("valid"):
            return {
                "ok": False,
                "error": cadence_result.get("reason"),
                "cadence": cadence_result,
            }

        bounded_max_entries = max(
            1, min(max_entries, _router_automation.MAX_RECONCILIATION_ENTRIES)
        )
        _max_tools_input = (
            _router_automation.MAX_RECONCILIATION_ENTRIES
            + _router_automation.MAX_RECONCILIATION_EXCLUDED_DETAIL
        )
        if tools is not None and len(tools) > _max_tools_input:
            return {
                "ok": False,
                "error": (
                    f"tools has {len(tools)} entries, exceeding the {_max_tools_input} bound"
                ),
                "cadence": cadence_result,
            }
        platform_filter = {str(p).strip().lower() for p in (platforms or []) if p}
        server_filter = {str(s).strip().lower() for s in (servers or []) if s}

        if tools:
            candidate_names = [str(t) for t in tools]
        else:
            candidate_names = []
            for name, backend_name in _tool_backend_names.items():
                if backend_name not in _BACKENDS:
                    continue
                if server_filter and backend_name.lower() not in server_filter:
                    continue
                platform = _server_platform(backend_name)
                if platform_filter and (platform or "").lower() not in platform_filter:
                    continue
                candidate_names.append(name)
            candidate_names.sort()

        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        excluded_total = 0
        for name in candidate_names:
            tool = _tool_index.get(name)
            server = _tool_backend_names.get(name)
            if tool is None or server not in _BACKENDS:
                excluded_total += 1
                if len(excluded) < _router_automation.MAX_RECONCILIATION_EXCLUDED_DETAIL:
                    excluded.append(
                        {"tool": name, "capability": "unknown", "reason": "unresolved_tool"}
                    )
                continue
            candidates.append(
                {
                    "tool": name,
                    "server": server,
                    "platform": _server_platform(server),
                    "capability": _tool_capability(tool),
                    "enabled": True,
                }
            )

        entries, capability_excluded, capability_excluded_total = (
            _router_automation.partition_reconciliation_candidates(
                candidates, max_entries=bounded_max_entries
            )
        )
        excluded.extend(capability_excluded)
        excluded_total += capability_excluded_total
        # Each source above is independently bounded to
        # MAX_RECONCILIATION_EXCLUDED_DETAIL, but their concatenation is not
        # -- re-cap the combined detail list so this router-native tool
        # (called directly, not proxied through _dispatch_tool's response
        # budgeting) never returns an unbounded payload. excluded_total
        # already reflects the true count regardless of this cap.
        if len(excluded) > _router_automation.MAX_RECONCILIATION_EXCLUDED_DETAIL:
            excluded = excluded[: _router_automation.MAX_RECONCILIATION_EXCLUDED_DETAIL]

        artifact: dict[str, Any] | None = None
        artifact_error: str | None = None
        try:
            payload = _router_automation.build_reconciliation_plan_payload(
                cadence=cadence_result,
                entries=entries,
                excluded=excluded,
                excluded_count=excluded_total,
            )
            built = _artifact_contracts.build_artifact(
                _artifact_contracts.ROUTER_RECONCILIATION_PLAN, payload
            )
            artifact = _artifact_contracts.to_json_dict(built)
        except _artifact_contracts.ArtifactValidationError as exc:
            artifact_error = str(exc)

        return {
            "ok": artifact_error is None,
            "cadence": cadence_result,
            "entries": entries,
            "excluded": excluded,
            "excluded_count": excluded_total,
            "dry_run": True,
            "artifact": artifact,
            "artifact_error": artifact_error,
        }


if _ROUTER_MODE == "direct":
    _register_direct_backend_tools()


# ── Observability label/classification helpers ───────────────────────────────
#
# Shared by MetricsMiddleware's label_resolver and AuditLogMiddleware's
# classifier so the two never diverge on "what backend tool actually ran".
# Bounded by construction: for invoke_tool/invoke_read_tool this resolves to
# the finite, already-loaded backend tool catalog (falling back to
# "unknown" for anything not found there); for every other router-native
# tool it is just that tool's own (fixed, small) name. Never reads any
# argument value beyond the single expected "name" key, and never reads
# result content at all.
def _router_call_labels(name: str, arguments: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve bounded ``(tool, backend, capability)`` labels for one call."""
    if name in {"invoke_tool", "invoke_read_tool"} and isinstance(arguments, dict):
        target = arguments.get("name")
        target_name = str(target) if target else None
        if target_name and target_name in _tool_index:
            backend = _tool_backend_names.get(target_name, "router")
            capability = _tool_capability(_tool_index[target_name])
            return (target_name, backend, capability)
        return (name, "router", "unknown")
    tool = mcp._tool_manager._tools.get(name)
    capability = _tool_capability(tool) if tool is not None else "unknown"
    return (name, "router", capability)


def _router_call_classification(name: str, arguments: dict[str, Any]) -> str:
    """Audit-log write/destructive classification -- reuses the same
    resolution as metrics so the two never disagree."""
    return _router_call_labels(name, arguments)[2]


def _router_call_target(name: str, arguments: dict[str, Any]) -> str | None:
    """Return only a catalog-resolved dispatch target for audit records."""
    if name not in {"invoke_tool", "invoke_read_tool"}:
        return None
    target, backend, _capability = _router_call_labels(name, arguments)
    return target if backend != "router" else "unknown"


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        AuditLogMiddleware,
        MacNormalizeMiddleware,
        MetricsMiddleware,
        NullStripMiddleware,
        RateLimitMiddleware,
        ResponseEnvelopeMiddleware,
        SecretTokenizeMiddleware,
        UnknownToolSuggestMiddleware,
        get_default_registry,
        install_middleware,
        metrics_enabled,
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

    _metrics_registry = get_default_registry()
    _metrics_on = metrics_enabled()
    middlewares = [
        NullStripMiddleware(),
        RateLimitMiddleware(
            rate=8.0,
            on_wait=_metrics_registry.record_rate_limit_wait if _metrics_on else None,
        ),
        UnknownToolSuggestMiddleware(
            lambda: mcp._tool_manager._tools,
            suggestion_provider=_suggest_router_tool,
        ),
        ResponseEnvelopeMiddleware(),
        SecretTokenizeMiddleware(),
        MetricsMiddleware(_metrics_registry, label_resolver=_router_call_labels),
        AuditLogMiddleware(
            classifier=_router_call_classification,
            target_resolver=_router_call_target,
        ),
    ]
    if os.getenv("CENTRALMCP_NORMALIZE_MACS", "").strip().lower() in {"1", "true", "yes"}:
        middlewares.append(MacNormalizeMiddleware())
    stable_list_tools(mcp)
    install_middleware(mcp, middlewares)
    from mcp_servers.shared import READ_ONLY, run_server
    run_server(mcp)
