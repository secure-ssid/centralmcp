"""MCP server — optional Juniper Mist backend (low-surface starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=mist

Auth/env:
  MIST_HOST       e.g. https://api.mist.com
  MIST_API_TOKEN  Mist API token
"""

from __future__ import annotations

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

mcp = FastMCP("mist-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."


def _mist_config() -> tuple[str | None, str | None]:
    import os

    host = os.getenv("MIST_HOST", "https://api.mist.com").strip().rstrip("/")
    token = os.getenv("MIST_API_TOKEN", "").strip()
    return (host or None, token or None)


def _normalize_mac(mac_address: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", mac_address).lower()
    if len(normalized) != 12:
        raise ValueError("MAC address must contain exactly 12 hex characters")
    return normalized


def _path_segment(value: str) -> str:
    return quote(value, safe="")


async def _mist_get_request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    bound: bool = True,
) -> dict[str, Any]:
    host, token = _mist_config()
    if not host or not token:
        return {"error": "Mist not configured. Set MIST_HOST and MIST_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/api/v1/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        host = validate_product_base_url(host, product="Mist")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{host}{path}"
    headers = {"Authorization": "Token " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            clean_params = {k: v for k, v in (params or {}).items() if v is not None}
            resp = await client.get(url, headers=headers, params=clean_params)
        payload = response_payload(resp)
        if bound:
            payload = bound_collection_response(payload, limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


async def _mist_write_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    *,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("mist_write")
    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    host, token = _mist_config()
    if not host or not token:
        return {"error": "Mist not configured. Set MIST_HOST and MIST_API_TOKEN."}
    try:
        safe_path = safe_api_path(path, ("/api/v1/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        host = validate_product_base_url(host, product="Mist")
    except ValueError as exc:
        return {"error": str(exc)}

    url = f"{host}{safe_path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    preview: dict[str, Any] = {
        "method": method,
        "path": safe_path,
        "url": url,
        "params": redact_sensitive(clean_params),
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

    headers = {"Authorization": "Token " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=clean_params,
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
    for key in ("items", "results", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _pick(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: data[field]
        for field in fields
        if field in data and data[field] not in (None, "")
    }


def _compact_site(site: Any) -> Any:
    if not isinstance(site, dict):
        return site
    return _pick(
        site,
        (
            "id",
            "name",
            "timezone",
            "country_code",
            "address",
            "latlng",
            "sitegroup_ids",
            "wifi_enabled",
        ),
    )


def _compact_client(client: Any) -> Any:
    if not isinstance(client, dict):
        return client
    return _pick(
        client,
        (
            "mac",
            "hostname",
            "ip",
            "username",
            "ap",
            "ap_id",
            "ap_name",
            "site_id",
            "ssid",
            "wlan_id",
            "vlan",
            "rssi",
            "snr",
            "band",
            "channel",
            "tx_rate",
            "rx_rate",
            "tx_bps",
            "rx_bps",
            "uptime",
            "last_seen",
            "health",
            "score",
            "connected",
            "assoc_time",
            "device",
            "os",
            "model",
        ),
    )


def _compact_wlan(wlan: Any) -> Any:
    if not isinstance(wlan, dict):
        return wlan
    return _pick(
        wlan,
        (
            "id",
            "name",
            "ssid",
            "enabled",
            "auth",
            "auth_servers",
            "vlan_id",
            "wlan_id",
            "template_id",
            "site_id",
        ),
    )


def _compact_alarm(alarm: Any) -> Any:
    if not isinstance(alarm, dict):
        return alarm
    return _pick(
        alarm,
        (
            "id",
            "type",
            "group",
            "severity",
            "timestamp",
            "last_seen",
            "count",
            "acked",
            "text",
            "reason",
            "device",
            "device_name",
            "ap",
            "client",
            "site_id",
        ),
    )


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
    out = await _mist_get_request(path, params, bound=False)
    if "data" in out:
        out["data"] = bound_collection_response(out["data"], limit=limit, offset=offset)
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_sites(
    org_id: str,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist org sites with compact ID, name, timezone, and location fields.

    Uses `GET /api/v1/orgs/{org_id}/sites`. Mist uses page-based pagination,
    so pass `limit` and `page` to move through larger orgs.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/sites",
        {"limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["sites"] = bound_collection_response(
            [_compact_site(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        if isinstance(out["sites"], dict):
            out["sites"]["server_page"] = max(1, page)
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_get_client(site_id: str, mac_address: str) -> dict[str, Any]:
    """Look up Mist wireless client health by site ID and MAC address.

    Uses `GET /api/v1/sites/{site_id}/stats/clients/{client_mac}` and returns
    compact health, AP, WLAN, RSSI, SNR, and identity fields.
    """
    try:
        normalized = _normalize_mac(mac_address)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/stats/clients/{normalized}"
    )
    if "data" in out:
        out["normalized_mac"] = normalized
        out["client"] = _compact_client(out["data"])
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_wlans(
    site_id: str,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist site WLANs with compact SSID, status, auth, and VLAN fields.

    Uses `GET /api/v1/sites/{site_id}/wlans`. Mist uses page-based pagination,
    so pass `limit` and `page` to move through larger sites.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/wlans",
        {"limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["wlans"] = bound_collection_response(
            [_compact_wlan(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        if isinstance(out["wlans"], dict):
            out["wlans"]["server_page"] = max(1, page)
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_alarms(
    site_id: str,
    severity: str | None = None,
    duration: str = "1d",
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
    search_after: str | None = None,
) -> dict[str, Any]:
    """List recent Mist site alarms with compact severity/time fields.

    Uses `GET /api/v1/sites/{site_id}/alarms/search`. Bound with `limit`;
    pass Mist `search_after` from a previous response to continue.
    """
    safe_limit = clamp_limit(limit, default=100)
    params = {
        "severity": severity,
        "limit": safe_limit,
        "start": start,
        "end": end,
        "duration": duration,
        "sort": "-timestamp",
        "search_after": search_after,
    }
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/alarms/search",
        params,
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["alarms"] = bound_collection_response(
            [_compact_alarm(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def mist_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to Mist with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/api/v1/*` paths on
    the configured Mist host. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    return await _mist_write_request(
        method,
        path,
        params=params,
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def mist_ack_alarm(
    site_id: str,
    alarm_id: str,
    note: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Acknowledge one Mist site alarm.

    Uses `POST /api/v1/sites/{site_id}/alarms/{alarm_id}/ack`. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    body = {"note": note} if note else None
    return await _mist_write_request(
        "POST",
        f"/api/v1/sites/{_path_segment(site_id)}/alarms/{_path_segment(alarm_id)}/ack",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def mist_unack_alarm(
    site_id: str,
    alarm_id: str,
    note: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Unacknowledge one Mist site alarm.

    Uses `POST /api/v1/sites/{site_id}/alarms/{alarm_id}/unack`. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    body = {"note": note} if note else None
    return await _mist_write_request(
        "POST",
        f"/api/v1/sites/{_path_segment(site_id)}/alarms/{_path_segment(alarm_id)}/unack",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def mist_delete_wlan(
    site_id: str,
    wlan_id: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete one Mist site WLAN.

    Uses `DELETE /api/v1/sites/{site_id}/wlans/{wlan_id}`. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    return await _mist_write_request(
        "DELETE",
        f"/api/v1/sites/{_path_segment(site_id)}/wlans/{_path_segment(wlan_id)}",
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
