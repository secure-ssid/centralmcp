"""MCP server — optional ArubaOS 8 / Mobility Conductor backend starter tools.

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=aos8

Auth/env:
  AOS8_BASE_URL   e.g. https://mobility-conductor.example.com
  AOS8_API_TOKEN  static bearer token
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    READ_ONLY,
    bound_collection_response,
    optional_product_write_blocked,
    optional_product_writes_allowed,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)

mcp = FastMCP("aos8-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."
_AP_FIELDS = (
    "Name",
    "name",
    "AP Name",
    "ap_name",
    "Group",
    "group",
    "IP Address",
    "ip_address",
    "Status",
    "status",
    "Flags",
    "flags",
    "Switch IP",
    "switch_ip",
    "Model",
    "model",
    "Serial #",
    "serial",
)
_CLIENT_FIELDS = (
    "Name",
    "name",
    "User Name",
    "username",
    "MAC Address",
    "mac",
    "IP Address",
    "ip_address",
    "AP Name",
    "ap_name",
    "SSID",
    "ssid",
    "Role",
    "role",
    "VLAN",
    "vlan",
    "Status",
    "status",
)


def _aos8_config() -> tuple[str | None, str | None]:
    import os

    base_url = os.getenv("AOS8_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("AOS8_API_TOKEN", "").strip()
    return (base_url or None, token or None)


def _strip_aos8_envelope(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return {key: value for key, value in data.items() if key not in {"_meta", "_global_result"}}


def _compact_aos8_data(data: Any, *, limit: int, offset: int = 0) -> Any:
    stripped = _strip_aos8_envelope(data)
    if isinstance(stripped, dict) and "_pagination" in stripped:
        return stripped
    return bound_collection_response(stripped, limit=limit, offset=offset)


def _compact_record(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return item
    compacted = {key: item[key] for key in fields if key in item}
    return compacted or item


def _compact_primary_list(data: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(data, list):
        return [_compact_record(item, fields) for item in data]
    if not isinstance(data, dict):
        return data
    out = dict(data)
    candidates = [
        (key, len(value))
        for key, value in out.items()
        if key != "_pagination" and isinstance(value, list)
    ]
    if not candidates:
        return out
    key = max(candidates, key=lambda kv: (kv[1], kv[0]))[0]
    out[key] = [_compact_record(item, fields) for item in out[key]]
    return out


@mcp.tool(annotations=READ_ONLY)
def aos8_status() -> dict[str, Any]:
    """Report whether AOS8 backend is configured."""
    base_url, token = _aos8_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
    }


@mcp.tool(annotations=READ_ONLY)
async def aos8_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to ArubaOS 8 API.

    Safety guard: only allows paths beginning with `/v1/`.
    List payloads are bounded with `limit` and `offset`.
    """
    base_url, token = _aos8_config()
    if not base_url or not token:
        return {"error": "AOS8 not configured. Set AOS8_BASE_URL and AOS8_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/v1/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="AOS8")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


@mcp.tool(annotations=READ_ONLY)
async def aos8_show_command(
    command: str,
    config_path: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Run a read-only AOS8 `show ...` command through the showcommand API."""
    normalized = command.strip()
    if not normalized.lower().startswith("show "):
        return {"error": f"Only 'show' commands are permitted. Received: {command!r}"}
    params: dict[str, Any] = {"command": normalized}
    if config_path:
        params["config_path"] = config_path
    out = await aos8_get("/v1/configuration/showcommand", params, limit=limit, offset=offset)
    if "data" in out:
        out["data"] = _compact_aos8_data(out["data"], limit=limit, offset=offset)
        out["command"] = normalized
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_aps(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List AOS8 AP inventory from `show ap database` with bounded output."""
    out = await aos8_show_command(
        "show ap database",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["aps"] = _compact_primary_list(out.pop("data"), _AP_FIELDS)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_clients(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List AOS8 clients from `show user-table` with bounded output."""
    out = await aos8_show_command(
        "show user-table",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["clients"] = _compact_primary_list(out.pop("data"), _CLIENT_FIELDS)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_ap_groups(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List AP-group configuration objects at an AOS8 hierarchy node."""
    out = await aos8_get(
        "/v1/configuration/object/ap_group",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["ap_groups"] = _compact_aos8_data(out.pop("data"), limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_ssid_profiles(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List SSID profile configuration objects at an AOS8 hierarchy node."""
    out = await aos8_get(
        "/v1/configuration/object/ssid_prof",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["ssid_profiles"] = _compact_aos8_data(out.pop("data"), limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to ArubaOS 8 with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/v1/*` paths on the
    configured ArubaOS 8 host. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("aos8_write")

    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token = _aos8_config()
    if not base_url or not token:
        return {"error": "AOS8 not configured. Set AOS8_BASE_URL and AOS8_API_TOKEN."}
    try:
        safe_path = safe_api_path(path, ("/v1/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="AOS8")
    except ValueError as exc:
        return {"error": str(exc)}

    url = f"{base_url}{safe_path}"
    preview = {
        "method": method,
        "path": safe_path,
        "url": url,
        "params": redact_sensitive(params or {}),
        "json": redact_sensitive(body),
    }
    if dry_run:
        return {
            "dry_run": True,
            **preview,
            "execute_hint": _EXECUTE_HINT,
        }
    if not confirm:
        return {
            "error": "confirm=True is required when dry_run=False.",
            "dry_run": True,
            **preview,
        }

    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params or {},
                json=body,
            )
        return {
            "status_code": resp.status_code,
            "data": redact_sensitive(response_payload(resp)),
            "url": url,
        }
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    from mcp_servers.shared import run_server

    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    run_server(mcp)
