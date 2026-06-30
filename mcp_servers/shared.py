"""Shared clients, helpers, and constants for all MCP servers."""
import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from mcp.types import ToolAnnotations

from pipeline.clients.central_client import CentralClient
from pipeline.clients.glp_client import GLPClient
from pipeline.clients.mcp_client import MCPClient
from pipeline.clients.token_manager import TokenManager
from pipeline.config import build_account_contexts

# ---------------------------------------------------------------------------
# MCP Tool Annotations — safety hints for MCP clients
# ---------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

DIAGNOSTIC = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Transport configuration
# ---------------------------------------------------------------------------

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _configure_http_transport(mcp_instance: Any, host: str, port: int) -> None:
    """Apply HTTP transport settings on SDK versions whose run() reads settings."""
    settings = getattr(mcp_instance, "settings", None)
    if settings is None:
        return
    settings.host = host
    settings.port = port

    transport_security = getattr(settings, "transport_security", None)
    if transport_security is None:
        return
    transport_security.enable_dns_rebinding_protection = _env_bool(
        "MCP_DNS_REBINDING_PROTECTION",
        transport_security.enable_dns_rebinding_protection,
    )
    allowed_hosts = _csv_env("MCP_ALLOWED_HOSTS")
    if allowed_hosts:
        transport_security.allowed_hosts = allowed_hosts
    allowed_origins = _csv_env("MCP_ALLOWED_ORIGINS")
    if allowed_origins:
        transport_security.allowed_origins = allowed_origins


def run_server(mcp_instance, default_port: int | None = None) -> None:
    """Run an MCP server with transport configured by environment.

    MCP_TRANSPORT: 'stdio' (default) or 'streamable-http'
    MCP_HOST: bind address (default 127.0.0.1)
    MCP_PORT: port (default 8000, or default_port if provided)
    MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS: comma-separated DNS rebinding allowlists
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp_instance.run()
    else:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", str(default_port or 8000)))
        _configure_http_transport(mcp_instance, host, port)
        mcp_instance.run(transport=transport)


# ---------------------------------------------------------------------------
# Lazy-initialised clients
# ---------------------------------------------------------------------------

_central_client: CentralClient | None = None
_mcp_client: MCPClient | None = None
_glp_client: GLPClient | None = None
_ENCODED_PATH_RESERVED = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)


def get_client() -> CentralClient:
    global _central_client
    if _central_client is None:
        creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
        source_ctx, _ = build_account_contexts(creds_path)
        tm = TokenManager(
            client_id=source_ctx.client_id,
            client_secret=source_ctx.client_secret,
            cache_key="source",
        )
        _central_client = CentralClient(base_url=source_ctx.base_url, token_manager=tm)
    return _central_client


def get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient(get_client())
    return _mcp_client


def get_glp_client() -> GLPClient:
    global _glp_client
    if _glp_client is None:
        creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
        _, target_ctx = build_account_contexts(creds_path)
        # GLP tokens live only ~15 min, so use a smaller refresh buffer than
        # the Central default (300s would burn a third of every window).
        tm = TokenManager(
            client_id=target_ctx.client_id,
            client_secret=target_ctx.client_secret,
            token_url=target_ctx.glp_token_url,
            cache_key="glp",
            expiry_buffer=60,
        )
        _glp_client = GLPClient(
            token_manager=tm,
            workspace_id=target_ctx.glp_workspace_id,
            base_url=target_ctx.glp_base_url,
        )
    return _glp_client


# ---------------------------------------------------------------------------
# Async troubleshooting helpers
# ---------------------------------------------------------------------------

_CX_TROUBLESHOOTING_BASE = "/network-troubleshooting/v1alpha1/cx"
_AOS_S_BASE = "/network-troubleshooting/v1alpha1/aos-s"
_GATEWAY_BASE = "/network-troubleshooting/v1alpha1/gateways"
_POLL_INTERVAL = 5
_POLL_MAX = 12
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def clamp_limit(limit: int | None, default: int = DEFAULT_LIST_LIMIT) -> int:
    """Clamp list/read limits to a safe, bounded range."""
    if limit is None:
        return default
    return max(1, min(limit, MAX_LIST_LIMIT))


def _truncate_text(value: Any, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def compact_http_error(resp: Any, endpoint: str | None = None, max_chars: int = 240) -> str:
    """Return a compact HTTP error message with bounded payload preview."""
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    where = f" at {endpoint}" if endpoint else ""
    return f"HTTP {resp.status_code}{where}: {_truncate_text(body, max_chars=max_chars)}"


def safe_api_path(path: str, allowed_prefixes: tuple[str, ...]) -> str:
    """Validate a user-supplied API path before appending it to an authenticated host."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("path must be a relative API path without scheme, host, query, or fragment")
    if _ENCODED_PATH_RESERVED.search(parsed.path):
        raise ValueError("path must not contain encoded dot, slash, or backslash characters")
    decoded = unquote(parsed.path)
    if _ENCODED_PATH_RESERVED.search(decoded):
        raise ValueError("path must not contain double-encoded dot, slash, or backslash characters")
    if "\\" in decoded:
        raise ValueError("path must not contain backslashes")
    if any(segment in (".", "..") for segment in decoded.split("/")):
        raise ValueError("path must not contain dot segments")
    if not decoded.startswith(allowed_prefixes):
        allowed = ", ".join(f"{prefix}*" for prefix in allowed_prefixes)
        raise ValueError(f"path must begin with one of: {allowed}")
    return decoded


def bound_collection_response(
    data: Any,
    *,
    limit: int,
    offset: int = 0,
    list_key: str | None = None,
) -> Any:
    """Slice the primary list in a JSON value to reduce MCP tool output size.

    - If ``data`` is a list, wraps as ``{"items": [...], "_pagination": ...}``.
    - If ``data`` is a dict, slices ``list_key`` or the longest top-level list
      (excluding ``_pagination``) and adds ``_pagination`` metadata.
    """
    lim = clamp_limit(limit)
    off = max(0, offset)
    if isinstance(data, list):
        total = len(data)
        page = data[off : off + lim]
        return {
            "items": page,
            "_pagination": {
                "offset": off,
                "limit": lim,
                "total": total,
                "truncated": total > off + len(page),
            },
        }
    if not isinstance(data, dict):
        return data
    out = {k: v for k, v in data.items() if k != "_pagination"}
    key = list_key
    if key is None:
        candidates = [(k, len(v)) for k, v in out.items() if isinstance(v, list)]
        if not candidates:
            return data
        key = max(candidates, key=lambda kv: (kv[1], kv[0]))[0]
    val = out.get(key)
    if not isinstance(val, list):
        return data
    total = len(val)
    page = val[off : off + lim]
    out[key] = page
    out["_pagination"] = {
        "offset": off,
        "limit": lim,
        "total": total,
        "truncated": total > off + len(page),
        "list_key": key,
    }
    return out


# ---------------------------------------------------------------------------
# Feature flag: bound list tool responses (A3)
# ---------------------------------------------------------------------------
#
# When ``CENTRALMCP_BOUND_LISTS`` is set to "1"/"true"/"yes", list tools
# that currently return a raw list[dict] wrap their response in
# ``{"items": [...], "_pagination": {...}}`` via
# bound_collection_response. Default OFF so existing clients that
# memoised the list shape don't break on upgrade. Flip on once
# consumers have moved to the wrapped shape.

_BOUND_LISTS_FLAG = "CENTRALMCP_BOUND_LISTS"


def _bound_lists_enabled() -> bool:
    return os.environ.get(_BOUND_LISTS_FLAG, "").lower() in ("1", "true", "yes")


def maybe_bound(
    data: Any,
    *,
    limit: int,
    offset: int = 0,
    list_key: str | None = None,
) -> Any:
    """Wrap ``data`` with bound_collection_response when ``CENTRALMCP_BOUND_LISTS``
    is enabled; otherwise return ``data`` unchanged.

    Lets list tools opt callers into the wrapped shape without a
    breaking change. Callers that always want the wrap should call
    ``bound_collection_response`` directly (several tools already do).
    """
    if not _bound_lists_enabled():
        return data
    return bound_collection_response(data, limit=limit, offset=offset, list_key=list_key)


async def atroubleshoot_poll(client: CentralClient, poll_url: str) -> dict[str, Any]:
    """Poll a Central troubleshooting async-operation without blocking the event loop."""
    result: dict[str, Any] = {}
    for _ in range(_POLL_MAX):
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            result = await client.aget(poll_url)
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}
        if result.get("status", "") in ("COMPLETED", "FAILED"):
            return result
    return result


async def atroubleshoot_async(
    client: CentralClient,
    endpoint: str,
    payload: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """Start and poll a Central troubleshooting task without blocking the event loop."""
    try:
        resp = await client._arequest("POST", endpoint, json=payload)
        if resp.status_code not in (200, 201, 202):
            errors.append(compact_http_error(resp))
            return {"status": None, "errors": errors}
        location = resp.headers.get("Location", "") or resp.json().get("location", "")
        if not location:
            errors.append("no Location header in async response")
            return {"status": None, "errors": errors}
        task_id = location.rstrip("/").split("/")[-1]
        poll_url = f"{endpoint}/async-operations/{task_id}"
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}
    result = await atroubleshoot_poll(client, poll_url)
    result["errors"] = errors
    return result


def resp_json(resp: Any) -> dict[str, Any]:
    """Return resp.json() or compact metadata if the body is not JSON."""
    try:
        return resp.json()
    except Exception:
        raw_text = resp.text or ""
        return {
            "status_code": resp.status_code,
            "text_preview": _truncate_text(raw_text),
            "text_length": len(raw_text),
        }


_DTYPE_MAP = {
    "AP": "aps",
    "ACCESS_POINT": "aps",
    "CX": "cx",
    "AOS_CX": "cx",
    "AOS-CX": "cx",
    "AOSCX": "cx",
    "AOS_S": "aos-s",
    "AOS-S": "aos-s",
    "AOSS": "aos-s",
    "GATEWAY": "gateways",
    "GW": "gateways",
}

# Model-series prefixes used to disambiguate generic SWITCH deviceTypes when
# firmware/softwareVersion is unavailable. AOS-CX vs AOS-S.
_CX_MODEL_SERIES = (
    "4100", "6000", "6100", "6200", "6300", "6400",
    "8100", "8320", "8325", "8360", "8400", "9300", "10000",
)
_AOS_S_MODEL_SERIES = ("2530", "2540", "2620", "2920", "2930", "3810", "5400")


def _classify_switch(device: dict[str, Any]) -> str:
    """Classify a generic SWITCH inventory record as 'cx' or 'aos-s'.

    Firmware/softwareVersion prefix is the strongest signal: AOS-CX versions
    look like 'FL.10.x'/'10.x'; AOS-S look like 'WC.16.x'/'KB.16.x'/'YA/YB/RA...'.
    Falls back to the model series, then a conservative default of 'cx' with a
    warning when ambiguous.
    """
    fw = (
        device.get("firmwareVersion")
        or device.get("softwareVersion")
        or device.get("swVersion")
        or ""
    )
    fw_upper = str(fw).upper()
    if fw_upper:
        # Strip any platform prefix like "FL." / "WC." to inspect the version.
        # AOS-CX versions have major "10" (e.g. "FL.10.16" / "10.16"); AOS-S
        # versions have major "16" (e.g. "WC.16.11" / "KB.16.10"). The numeric
        # major is the strongest signal, so check it before the prefix.
        parts = fw_upper.split(".")
        prefix = parts[0]
        # The major version is the first part that is all digits.
        major = next((p for p in parts if p.isdigit()), "")
        if major == "10":
            return "cx"
        if major == "16":
            return "aos-s"
        # No recognisable major: a two-letter alpha platform prefix
        # (YA/YB/RA/...) is AOS-S styling.
        if prefix and len(prefix) == 2 and prefix.isalpha():
            return "aos-s"

    model = str(device.get("model") or device.get("deviceModel") or "")
    if any(series in model for series in _CX_MODEL_SERIES):
        return "cx"
    if any(series in model for series in _AOS_S_MODEL_SERIES):
        return "aos-s"

    logging.getLogger(__name__).warning(
        "Ambiguous switch type for serial=%s (firmware=%r model=%r); defaulting to 'cx'",
        device.get("serialNumber", ""),
        fw,
        model,
    )
    return "cx"


def device_type_for_troubleshoot(serial_number: str, device_type: str | None) -> str | None:
    """Auto-detect device type from inventory if not supplied.

    Returns lowercase URL-ready device type: "aps", "cx", "aos-s", "gateways",
    or None.
    """
    if device_type:
        upper = device_type.upper()
        return _DTYPE_MAP.get(upper, upper.lower())
    device = get_mcp_client().get_device_by_serial(serial_number)
    if not device:
        return None
    raw = device.get("deviceType", "")
    if "ACCESS_POINT" in raw or raw == "AP":
        return "aps"
    if "SWITCH" in raw:
        # SWITCH covers both AOS-CX and AOS-S; disambiguate from the record.
        return _classify_switch(device)
    if "GATEWAY" in raw:
        return "gateways"
    return None
