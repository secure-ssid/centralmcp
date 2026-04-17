"""Shared clients, helpers, and constants for all MCP servers."""
import logging
import os
import time
from typing import Any
from urllib.parse import quote

from pipeline.clients.central_client import CentralClient
from pipeline.clients.glp_client import GLPClient
from pipeline.clients.mcp_client import MCPClient
from pipeline.clients.token_manager import TokenManager
from pipeline.config import build_account_contexts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Lazy-initialised clients
# ---------------------------------------------------------------------------

_central_client: CentralClient | None = None
_mcp_client: MCPClient | None = None
_glp_client: GLPClient | None = None


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
            cache_key="glp",
            expiry_buffer=60,
        )
        _glp_client = GLPClient(
            token_manager=tm,
            workspace_id=target_ctx.glp_workspace_id,
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


def cx_poll(client: CentralClient, serial: str, operation: str, task_id: str) -> dict[str, Any]:
    endpoint = f"{_CX_TROUBLESHOOTING_BASE}/{serial}/{operation}/async-operations/{task_id}"
    result: dict[str, Any] = {}
    for _ in range(_POLL_MAX):
        time.sleep(_POLL_INTERVAL)
        try:
            result = client.get(endpoint)
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}
        if result.get("status", "") in ("COMPLETED", "FAILED"):
            return result
    return result


def troubleshoot_poll(client: CentralClient, poll_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for _ in range(_POLL_MAX):
        time.sleep(_POLL_INTERVAL)
        try:
            result = client.get(poll_url)
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}
        if result.get("status", "") in ("COMPLETED", "FAILED"):
            return result
    return result


def troubleshoot_async(
    client: CentralClient,
    endpoint: str,
    payload: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    try:
        resp = client._request("POST", endpoint, json=payload)
        if resp.status_code not in (200, 201, 202):
            errors.append(compact_http_error(resp))
            return {"status": None, "errors": errors}
        location = resp.headers.get("Location", "") or resp.json().get("location", "")
        task_id = location.rstrip("/").split("/")[-1]
        poll_url = f"{endpoint}/async-operations/{task_id}"
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}
    result = troubleshoot_poll(client, poll_url)
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


def device_type_for_troubleshoot(serial_number: str, device_type: str | None) -> str | None:
    """Auto-detect device type from inventory if not supplied."""
    if device_type:
        return device_type.upper()
    device = get_mcp_client().get_device_by_serial(serial_number)
    if not device:
        return None
    raw = device.get("deviceType", "")
    if "ACCESS_POINT" in raw or raw == "AP":
        return "AP"
    if "SWITCH" in raw:
        return "SWITCH"
    if "GATEWAY" in raw:
        return "GATEWAY"
    return None
