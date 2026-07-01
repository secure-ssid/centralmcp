"""MCP server — optional HPE Aruba EdgeConnect backend starter tools.

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=edgeconnect

Auth/env:
  EDGECONNECT_BASE_URL    e.g. https://orchestrator.example.com
  EDGECONNECT_API_TOKEN   API token
  EDGECONNECT_AUTH_HEADER Header name; defaults to Authorization
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

mcp = FastMCP("edgeconnect-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."
_APPLIANCE_FIELDS = (
    "id",
    "nePk",
    "hostName",
    "hostname",
    "name",
    "site",
    "model",
    "serial",
    "serialNumber",
    "softwareVersion",
    "ipAddress",
    "state",
    "status",
)


def _edgeconnect_config() -> tuple[str | None, str | None, str]:
    import os

    base_url = os.getenv("EDGECONNECT_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("EDGECONNECT_API_TOKEN", "").strip()
    header = os.getenv("EDGECONNECT_AUTH_HEADER", "Authorization").strip() or "Authorization"
    return (base_url or None, token or None, header)


def _compact_record(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return item
    return {key: item[key] for key in fields if key in item}


def _compact_collection(data: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(data, list):
        return [_compact_record(item, fields) for item in data]
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in ("items", "results", "data"):
        if isinstance(out.get(key), list):
            out[key] = [_compact_record(item, fields) for item in out[key]]
            break
    return out


@mcp.tool(annotations=READ_ONLY)
def edgeconnect_status() -> dict[str, Any]:
    """Report whether EdgeConnect backend is configured."""
    base_url, token, header = _edgeconnect_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
        "auth_header": header,
    }


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to EdgeConnect Orchestrator API.

    Safety guard: only allows paths beginning with `/gms/rest/` or `/rest/json/`.
    List payloads are bounded with `limit` and `offset`.
    """
    base_url, token, header = _edgeconnect_config()
    if not base_url or not token:
        return {
            "error": "EdgeConnect not configured. Set EDGECONNECT_BASE_URL and "
            "EDGECONNECT_API_TOKEN."
        }
    try:
        path = safe_api_path(path, ("/gms/rest/", "/rest/json/"))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="EdgeConnect")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    auth_value = "Bearer " + token if header.lower() == "authorization" else token
    headers = {header: auth_value, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_appliances(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List EdgeConnect Orchestrator appliances with compact inventory fields."""
    out = await edgeconnect_get("/gms/rest/appliance", limit=limit, offset=offset)
    if "data" in out:
        out["appliances"] = _compact_collection(out.pop("data"), _APPLIANCE_FIELDS)
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to EdgeConnect with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/gms/rest/*` or
    `/rest/json/*` paths on the configured Orchestrator host. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_write")

    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token, header = _edgeconnect_config()
    if not base_url or not token:
        return {
            "error": "EdgeConnect not configured. Set EDGECONNECT_BASE_URL and "
            "EDGECONNECT_API_TOKEN."
        }
    try:
        safe_path = safe_api_path(path, ("/gms/rest/", "/rest/json/"))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="EdgeConnect")
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

    auth_value = "Bearer " + token if header.lower() == "authorization" else token
    headers = {header: auth_value, "Accept": "application/json"}
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
