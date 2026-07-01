"""MCP server — optional HPE Juniper Apstra backend (low-surface starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=apstra

Auth/env:
  APSTRA_BASE_URL   e.g. https://apstra.example.com
  APSTRA_API_TOKEN  static bearer token
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

mcp = FastMCP("apstra-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."


def _apstra_config() -> tuple[str | None, str | None]:
    import os

    base_url = os.getenv("APSTRA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("APSTRA_API_TOKEN", "").strip()
    return (base_url or None, token or None)


@mcp.tool(annotations=READ_ONLY)
def apstra_status() -> dict[str, Any]:
    """Report whether Apstra backend is configured."""
    base_url, token = _apstra_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
    }


@mcp.tool(annotations=READ_ONLY)
async def apstra_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to Apstra API.

    Safety guard: only allows paths beginning with `/api/`.
    List payloads are bounded with `limit` and `offset`.
    """
    base_url, token = _apstra_config()
    if not base_url or not token:
        return {"error": "Apstra not configured. Set APSTRA_BASE_URL and APSTRA_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}

    try:
        base_url = validate_product_base_url(base_url, product="Apstra")
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


@mcp.tool(annotations=DESTRUCTIVE)
async def apstra_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to Apstra with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/api/*` paths on the
    configured Apstra host. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("apstra_write")

    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token = _apstra_config()
    if not base_url or not token:
        return {"error": "Apstra not configured. Set APSTRA_BASE_URL and APSTRA_API_TOKEN."}
    try:
        safe_path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="Apstra")
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
