"""MCP server — optional ClearPass backend (low-surface starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=clearpass

Auth/env:
  CLEARPASS_BASE_URL   e.g. https://clearpass.example.com
  CLEARPASS_API_TOKEN  static bearer token
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    bound_collection_response,
    clamp_limit,
    optional_product_write_blocked,
    optional_product_writes_allowed,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)

mcp = FastMCP("clearpass-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."


def _clearpass_config() -> tuple[str | None, str | None]:
    import os

    base_url = os.getenv("CLEARPASS_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("CLEARPASS_API_TOKEN", "").strip()
    return (base_url or None, token or None)


def _auth_header(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token
    return "Bearer " + token


def _normalize_mac(mac_address: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", mac_address).lower()
    if len(normalized) != 12:
        raise ValueError("MAC address must contain exactly 12 hex characters")
    return normalized


def _path_segment(value: str) -> str:
    return quote(value, safe="")


async def _clearpass_get_request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    bound: bool = True,
) -> dict[str, Any]:
    base_url, token = _clearpass_config()
    if not base_url or not token:
        return {
            "error": "ClearPass not configured. Set CLEARPASS_BASE_URL and CLEARPASS_API_TOKEN."
        }
    try:
        path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="ClearPass")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    headers = {"Authorization": _auth_header(token), "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = response_payload(resp)
        if bound:
            payload = bound_collection_response(payload, limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


async def _clearpass_write_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    *,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("clearpass_write")
    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token = _clearpass_config()
    if not base_url or not token:
        return {
            "error": "ClearPass not configured. Set CLEARPASS_BASE_URL and CLEARPASS_API_TOKEN."
        }
    try:
        safe_path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="ClearPass")
    except ValueError as exc:
        return {"error": str(exc)}

    url = f"{base_url}{safe_path}"
    preview: dict[str, Any] = {
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

    headers = {"Authorization": _auth_header(token), "Accept": "application/json"}
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


def _extract_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("items", "results", "data", "_embedded"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _first_item(data: Any) -> Any:
    if isinstance(data, dict) and not any(
        isinstance(data.get(key), list) for key in ("items", "results", "data")
    ):
        return data
    items = _extract_items(data)
    return items[0] if items else None


def _pick(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: data[field]
        for field in fields
        if field in data and data[field] not in (None, "")
    }


def _compact_endpoint(endpoint: Any) -> Any:
    if not isinstance(endpoint, dict):
        return endpoint
    return _pick(
        endpoint,
        (
            "id",
            "mac_address",
            "status",
            "enabled",
            "profile_status",
            "profile",
            "profile_name",
            "device_category",
            "device_family",
            "device_name",
            "hostname",
            "ip_address",
            "username",
            "description",
            "updated_at",
            "created_at",
            "last_seen",
        ),
    )


def _compact_session(session: Any) -> Any:
    if not isinstance(session, dict):
        return session
    out = _pick(
        session,
        (
            "id",
            "session_id",
            "username",
            "user_name",
            "mac_address",
            "calling_station_id",
            "endpoint_mac_address",
            "nasipaddress",
            "nas_ip",
            "nas_identifier",
            "nas_port_id",
            "auth_status",
            "service_name",
            "enforcement_profile",
            "acctstarttime",
            "timestamp",
        ),
    )
    for key in (
        "reason",
        "auth_error",
        "error_message",
        "reply_message",
        "alert_message",
        "result",
    ):
        if session.get(key):
            out["reason"] = session[key]
            break
    return out


def _compact_network_device(device: Any) -> Any:
    if not isinstance(device, dict):
        return device
    return _pick(
        device,
        (
            "id",
            "name",
            "ip_address",
            "vendor_name",
            "coa_capable",
            "coa_port",
            "status",
            "enabled",
            "description",
        ),
    )


def _compact_guest(guest: Any) -> Any:
    if not isinstance(guest, dict):
        return guest
    return _pick(
        guest,
        (
            "id",
            "username",
            "email",
            "visitor_name",
            "name",
            "role_name",
            "sponsor_name",
            "sponsor_email",
            "enabled",
            "expire_time",
            "created_at",
            "updated_at",
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def clearpass_status() -> dict[str, Any]:
    """Report whether ClearPass backend is configured."""
    base_url, token = _clearpass_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
    }


@mcp.tool(annotations=READ_ONLY)
async def clearpass_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to ClearPass REST API.

    Safety guard: only allows paths beginning with `/api/`.
    List payloads are bounded with `limit` and `offset`.
    """
    out = await _clearpass_get_request(path, params, bound=False)
    if "data" in out:
        out["data"] = bound_collection_response(out["data"], limit=limit, offset=offset)
    return out


@mcp.tool(annotations=READ_ONLY)
async def clearpass_get_endpoint_by_mac(mac_address: str) -> dict[str, Any]:
    """Look up one ClearPass endpoint by MAC address.

    Accepts colon, dash, dotted, or compact MAC input and queries
    `/api/endpoint/mac-address/{mac}`. Returns compact endpoint/profile/status
    fields instead of the full endpoint record.
    """
    try:
        normalized = _normalize_mac(mac_address)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _clearpass_get_request(f"/api/endpoint/mac-address/{normalized}")
    if "data" in out:
        out["normalized_mac"] = normalized
        out["endpoint"] = _compact_endpoint(_first_item(out["data"]))
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def clearpass_list_auth_failures(
    limit: int = 25,
    offset: int = 0,
    auth_status: str = "FAILED",
) -> dict[str, Any]:
    """List recent ClearPass authentication failures.

    Queries `/api/session` with a bounded page and an `auth_status` filter
    (default `FAILED`). Returns compact username, MAC, NAD, status, and reason
    fields for troubleshooting.
    """
    safe_limit = clamp_limit(limit, default=25)
    params = {
        "filter": json.dumps({"auth_status": auth_status}, separators=(",", ":")),
        "sort": "-acctstarttime",
        "offset": max(0, offset),
        "limit": safe_limit,
        "calculate_count": "false",
    }
    out = await _clearpass_get_request(
        "/api/session",
        params,
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        items = [_compact_session(item) for item in _extract_items(out["data"])]
        out["data"] = bound_collection_response(items, limit=safe_limit, offset=0)
        if isinstance(out["data"], dict):
            out["data"]["filter"] = {"auth_status": auth_status}
            out["data"]["server_offset"] = max(0, offset)
    return out


@mcp.tool(annotations=READ_ONLY)
async def clearpass_get_network_device(
    name: str | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Get a ClearPass network device (NAD) by name or numeric ID.

    Uses `/api/network-device/name/{name}` when `name` is provided, otherwise
    `/api/network-device/{device_id}`. Returns compact RADIUS/TACACS status
    fields useful for NAD troubleshooting.
    """
    if bool(name) == bool(device_id):
        return {"error": "Provide exactly one of name or device_id."}
    if name:
        path = f"/api/network-device/name/{_path_segment(name)}"
    else:
        path = f"/api/network-device/{_path_segment(device_id or '')}"
    out = await _clearpass_get_request(path)
    if "data" in out:
        out["network_device"] = _compact_network_device(_first_item(out["data"]))
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def clearpass_find_guest(
    query: str,
    field: str = "username",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Find ClearPass guest accounts by username, email, visitor_name, or name.

    `username` uses the direct `/api/guest/username/{username}` lookup. Other
    fields query `/api/guest` with a JSON filter. Results are compact and
    bounded by `limit` / `offset`.
    """
    allowed_fields = {"username", "email", "visitor_name", "name"}
    if field not in allowed_fields:
        return {"error": f"field must be one of: {', '.join(sorted(allowed_fields))}"}
    if field == "username":
        out = await _clearpass_get_request(f"/api/guest/username/{_path_segment(query)}")
        if "data" in out:
            out["guests"] = bound_collection_response(
                [_compact_guest(_first_item(out["data"]))],
                limit=1,
                offset=0,
            )
            del out["data"]
        return out

    safe_limit = clamp_limit(limit, default=25)
    params = {
        "filter": json.dumps({field: query}, separators=(",", ":")),
        "offset": max(0, offset),
        "limit": safe_limit,
        "calculate_count": "false",
    }
    out = await _clearpass_get_request("/api/guest", params, limit=safe_limit, offset=0)
    if "data" in out:
        out["guests"] = bound_collection_response(
            [_compact_guest(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        if isinstance(out["guests"], dict):
            out["guests"]["filter"] = {field: query}
            out["guests"]["server_offset"] = max(0, offset)
        del out["data"]
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def clearpass_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to ClearPass with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/api/*` paths on the
    configured ClearPass host. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    return await _clearpass_write_request(
        method,
        path,
        params=params,
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def clearpass_update_endpoint_attributes(
    mac_address: str,
    attributes: dict[str, Any],
    change_of_authorization: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Patch ClearPass endpoint attributes by MAC for lab workflows.

    Uses `PATCH /api/endpoint/mac-address/{mac}` with an `attributes` object.
    Defaults to `dry_run=True`; execution requires `dry_run=False` and
    `confirm=True`.
    """
    try:
        normalized = _normalize_mac(mac_address)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _clearpass_write_request(
        "PATCH",
        f"/api/endpoint/mac-address/{normalized}",
        params={"change_of_authorization": str(change_of_authorization).lower()},
        body={"attributes": attributes},
        dry_run=dry_run,
        confirm=confirm,
    )
    out["normalized_mac"] = normalized
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def clearpass_delete_endpoint(
    mac_address: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a ClearPass endpoint by MAC address.

    Uses `DELETE /api/endpoint/mac-address/{mac}`. Defaults to `dry_run=True`;
    execution requires `dry_run=False` and `confirm=True`.
    """
    try:
        normalized = _normalize_mac(mac_address)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _clearpass_write_request(
        "DELETE",
        f"/api/endpoint/mac-address/{normalized}",
        dry_run=dry_run,
        confirm=confirm,
    )
    out["normalized_mac"] = normalized
    return out


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def clearpass_set_guest_enabled(
    enabled: bool,
    username: str | None = None,
    guest_id: str | None = None,
    change_of_authorization: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Enable or disable a ClearPass guest account by username or ID.

    Uses `PATCH /api/guest/username/{username}` or `/api/guest/{guest_id}` with
    `{"enabled": ...}`. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    if bool(username) == bool(guest_id):
        return {"error": "Provide exactly one of username or guest_id."}
    if username:
        path = f"/api/guest/username/{_path_segment(username)}"
    else:
        path = f"/api/guest/{_path_segment(guest_id or '')}"
    return await _clearpass_write_request(
        "PATCH",
        path,
        params={"change_of_authorization": str(change_of_authorization).lower()},
        body={"enabled": enabled},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def clearpass_delete_guest(
    username: str | None = None,
    guest_id: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a ClearPass guest account by username or ID.

    Uses `DELETE /api/guest/username/{username}` or `/api/guest/{guest_id}`.
    Defaults to `dry_run=True`; execution requires `dry_run=False` and
    `confirm=True`.
    """
    if bool(username) == bool(guest_id):
        return {"error": "Provide exactly one of username or guest_id."}
    if username:
        path = f"/api/guest/username/{_path_segment(username)}"
    else:
        path = f"/api/guest/{_path_segment(guest_id or '')}"
    return await _clearpass_write_request(
        "DELETE",
        path,
        dry_run=dry_run,
        confirm=confirm,
    )


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
