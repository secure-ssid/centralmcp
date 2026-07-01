"""MCP server — optional Juniper Mist backend (low-surface starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=mist

Auth/env:
  MIST_HOST       e.g. https://api.mist.com
  MIST_API_TOKEN  Mist API token
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    READ_ONLY,
    bound_collection_response,
    response_payload,
    safe_api_path,
)

mcp = FastMCP("mist-core")


def _mist_config() -> tuple[str | None, str | None]:
    import os

    host = os.getenv("MIST_HOST", "https://api.mist.com").strip().rstrip("/")
    token = os.getenv("MIST_API_TOKEN", "").strip()
    return (host or None, token or None)


@mcp.tool(annotations=READ_ONLY)
def mist_status() -> dict[str, Any]:
    """Report whether Mist backend is configured."""
    host, token = _mist_config()
    return {
        "configured": bool(host and token),
        "host": host,
        "has_token": bool(token),
    }


@mcp.tool(annotations=READ_ONLY)
async def mist_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to Mist API.

    Safety guard: only allows paths beginning with `/api/v1/`.
    List payloads are bounded with `limit` and `offset`.
    """
    host, token = _mist_config()
    if not host or not token:
        return {"error": "Mist not configured. Set MIST_HOST and MIST_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/api/v1/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}

    url = f"{host}{path}"
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
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
