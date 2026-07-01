"""MCP server - optional HPE Aruba UXI backend (read-only starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=uxi

Auth/env:
  UXI_CLIENT_ID      GreenLake OAuth2 client ID
  UXI_CLIENT_SECRET  GreenLake OAuth2 client secret
  UXI_BASE_URL       optional, defaults to HPE UXI v1alpha1 API
  UXI_TOKEN_URL      optional, defaults to HPE GreenLake SSO token URL
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    READ_ONLY,
    bound_collection_response,
    clamp_limit,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)

mcp = FastMCP("uxi-core")

_DEFAULT_UXI_BASE_URL = "https://api.capenetworks.com/networking-uxi/v1alpha1"
_DEFAULT_TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
_LIST_PATHS = {
    "/agents",
    "/agent-group-assignments",
    "/groups",
    "/network-group-assignments",
    "/sensors",
    "/sensor-group-assignments",
    "/service-test-group-assignments",
    "/service-tests",
    "/wired-networks",
    "/wireless-networks",
}
_TOKEN_CACHE: dict[str, Any] = {}


def _uxi_config() -> tuple[str | None, str | None, str, str]:
    import os

    client_id = os.getenv("UXI_CLIENT_ID", "").strip()
    client_secret = os.getenv("UXI_CLIENT_SECRET", "").strip()
    base_url = os.getenv("UXI_BASE_URL", _DEFAULT_UXI_BASE_URL).strip().rstrip("/")
    token_url = os.getenv("UXI_TOKEN_URL", _DEFAULT_TOKEN_URL).strip()
    return client_id or None, client_secret or None, base_url, token_url


def _uxi_limit(limit: int | None) -> int:
    return min(clamp_limit(limit, default=50), 100)


def _cursor_params(next_cursor: str | None, page_size: int) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": _uxi_limit(page_size)}
    if next_cursor:
        params["next"] = next_cursor
    return params


def _path_segment(value: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in text):
        raise ValueError("UXI resource IDs must be 1-128 ASCII letters, numbers, underscores, or dashes.")
    return quote(text, safe="")


def _safe_uxi_path(path: str) -> str:
    safe_path = safe_api_path(path, ("/",))
    if safe_path in _LIST_PATHS:
        return quote(safe_path, safe="/")
    parts = safe_path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "sensors" and parts[2] == "status":
        return f"/sensors/{_path_segment(parts[1])}/status"
    allowed = ", ".join(sorted(_LIST_PATHS))
    raise ValueError(f"path must be one of: {allowed}, or /sensors/{{id}}/status")


def _pick(record: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(record, dict):
        return record
    return {field: record[field] for field in fields if field in record and record[field] not in (None, "")}


def _compact_items(data: Any, fields: tuple[str, ...] | None) -> Any:
    if not fields:
        return data
    if isinstance(data, list):
        return [_pick(item, fields) for item in data]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        out = dict(data)
        out["items"] = [_pick(item, fields) for item in data["items"]]
        return out
    return _pick(data, fields)


async def _uxi_access_token(client_id: str, client_secret: str, token_url: str) -> str:
    token_url = validate_product_base_url(token_url, product="UXI token URL")
    now = time.time()
    cache_key = f"{token_url}:{client_id}"
    if (
        _TOKEN_CACHE.get("key") == cache_key
        and _TOKEN_CACHE.get("token")
        and float(_TOKEN_CACHE.get("expires_at", 0)) > now + 60
    ):
        return str(_TOKEN_CACHE["token"])

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    payload = response_payload(resp)
    if resp.status_code >= 400:
        raise RuntimeError(f"UXI token request failed with HTTP {resp.status_code}: {redact_sensitive(payload)}")
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise RuntimeError("UXI token response did not include access_token.")
    expires_in = int(payload.get("expires_in") or 3600)
    _TOKEN_CACHE.update(
        {
            "key": cache_key,
            "token": payload["access_token"],
            "expires_at": now + max(60, expires_in),
        }
    )
    return str(payload["access_token"])


async def _uxi_get_request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    fields: tuple[str, ...] | None = None,
    bound: bool = True,
) -> dict[str, Any]:
    client_id, client_secret, base_url, token_url = _uxi_config()
    if not client_id or not client_secret:
        return {"error": "UXI not configured. Set UXI_CLIENT_ID and UXI_CLIENT_SECRET."}
    try:
        base_url = validate_product_base_url(base_url, product="UXI")
        safe_path = _safe_uxi_path(path)
    except ValueError as exc:
        return {"error": str(exc)}

    try:
        token = await _uxi_access_token(client_id, client_secret, token_url)
        url = f"{base_url}{safe_path}"
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                params=clean_params,
            )
        payload = response_payload(resp)
        payload = _compact_items(payload, fields)
        if bound:
            payload = bound_collection_response(payload, limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": f"{type(exc).__name__}: connection or protocol error", "url": f"{base_url}{path}"}
    except Exception as exc:
        return {"error": str(exc), "url": f"{base_url}{path}"}


@mcp.tool(annotations=READ_ONLY)
def uxi_status() -> dict[str, Any]:
    """Report whether the optional UXI backend has OAuth credentials configured."""
    client_id, client_secret, base_url, token_url = _uxi_config()
    return {
        "configured": bool(client_id and client_secret),
        "has_client_id": bool(client_id),
        "has_client_secret": bool(client_secret),
        "base_url": base_url,
        "token_url": token_url,
        "tools": [
            "uxi_get",
            "uxi_list_sensors",
            "uxi_get_sensor_status",
            "uxi_list_agents",
            "uxi_list_groups",
            "uxi_list_wired_networks",
            "uxi_list_wireless_networks",
            "uxi_list_service_tests",
        ],
    }


@mcp.tool(annotations=READ_ONLY)
async def uxi_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a guarded read-only GET against selected UXI v1alpha1 paths."""
    return await _uxi_get_request(path, params, limit=limit, offset=offset)


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_sensors(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI sensors with compact identity, model, MAC, group, and location fields."""
    return await _uxi_get_request(
        "/sensors",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=(
            "id",
            "name",
            "serial",
            "type",
            "modelNumber",
            "ethernetMacAddress",
            "wifiMacAddress",
            "groupName",
            "groupPath",
            "latitude",
            "longitude",
            "addressNote",
            "notes",
            "pcapMode",
        ),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_get_sensor_status(sensor_id: str) -> dict[str, Any]:
    """Get online/testing status and active issues for a UXI sensor."""
    try:
        path = f"/sensors/{_path_segment(sensor_id)}/status"
    except ValueError as exc:
        return {"error": str(exc)}
    return await _uxi_get_request(path, fields=("isOnline", "isTesting", "issues"), bound=False)


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_agents(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI agents with compact identity, model, MAC, group, and notes fields."""
    return await _uxi_get_request(
        "/agents",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=(
            "id",
            "name",
            "serial",
            "type",
            "modelNumber",
            "ethernetMacAddress",
            "wifiMacAddress",
            "groupName",
            "groupPath",
            "notes",
            "pcapMode",
        ),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_groups(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI groups with id, name, path, and parent group."""
    return await _uxi_get_request(
        "/groups",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=("id", "name", "path", "parent"),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_wired_networks(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI wired networks."""
    return await _uxi_get_request(
        "/wired-networks",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=(
            "id",
            "name",
            "type",
            "security",
            "vLanId",
            "ipVersion",
            "externalConnectivity",
            "dnsLookupDomain",
            "disableEdns",
            "useDns64",
            "createdAt",
            "updatedAt",
        ),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_wireless_networks(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI wireless networks."""
    return await _uxi_get_request(
        "/wireless-networks",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=(
            "id",
            "name",
            "type",
            "ssid",
            "security",
            "hidden",
            "ipVersion",
            "externalConnectivity",
            "dnsLookupDomain",
            "disableEdns",
            "useDns64",
            "createdAt",
            "updatedAt",
        ),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_service_tests(next_cursor: str | None = None, page_size: int = 50) -> dict[str, Any]:
    """List UXI service tests."""
    return await _uxi_get_request(
        "/service-tests",
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=("id", "name", "type", "category", "target", "template", "isEnabled"),
    )


async def _uxi_list_assignments(path: str, next_cursor: str | None, page_size: int) -> dict[str, Any]:
    return await _uxi_get_request(
        path,
        _cursor_params(next_cursor, page_size),
        limit=page_size,
        fields=("id", "type", "agent", "sensor", "network", "serviceTest", "group"),
    )


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_agent_group_assignments(
    next_cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    """List UXI agent-to-group assignments."""
    return await _uxi_list_assignments("/agent-group-assignments", next_cursor, page_size)


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_sensor_group_assignments(
    next_cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    """List UXI sensor-to-group assignments."""
    return await _uxi_list_assignments("/sensor-group-assignments", next_cursor, page_size)


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_network_group_assignments(
    next_cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    """List UXI network-to-group assignments."""
    return await _uxi_list_assignments("/network-group-assignments", next_cursor, page_size)


@mcp.tool(annotations=READ_ONLY)
async def uxi_list_service_test_group_assignments(
    next_cursor: str | None = None,
    page_size: int = 50,
) -> dict[str, Any]:
    """List UXI service-test-to-group assignments."""
    return await _uxi_list_assignments("/service-test-group-assignments", next_cursor, page_size)


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    stable_list_tools(mcp)
    from mcp_servers.shared import run_server
    run_server(mcp)
