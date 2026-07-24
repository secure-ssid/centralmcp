"""MCP server — optional Juniper Mist backend (low-surface starter tools, 26 tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=mist

Auth/env:
  MIST_HOST       e.g. https://api.mist.com
  MIST_API_TOKEN  Mist API token

Covers the generic passthrough/WLAN/alarm tools plus typed, bounded workflow
tools for: NAC/Access Assurance (nactags/nacportals/usermacs, plus NAC IDP
realm mappings read from org settings), Marvis AI (client telemetry, client
experience insights, device event search, org Marvis settings), org
inventory and device claims, Wired Assurance switch/port stats, and WAN
Assurance gateway (SRX/SSR) stats. Endpoints and field names verified
directly against the mistsys/mist_openapi spec (mist.openapi.yaml) at
commit f374cffdd5a275c7954645a306fcab7f1227e7a3 (tag 2606.1.1, 2026-07-10).
See individual tool docstrings for the underlying `/api/v1/*` endpoints and
any remaining live-instance verification caveats.
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
    platform_write_blocked as _platform_write_blocked,
    platform_writes_allowed as _platform_writes_allowed,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)

mcp = FastMCP("mist-core")


def optional_product_writes_allowed() -> bool:
    return _platform_writes_allowed("mist")


def optional_product_write_blocked(tool_name: str) -> dict[str, str]:
    return _platform_write_blocked("mist", tool_name)


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


def _compact_nac_tag(tag: Any) -> Any:
    if not isinstance(tag, dict):
        return tag
    # Verified against mist_openapi `nac_tag` schema — the VLAN field is
    # named `vlan` (string), not `vlan_id`.
    return _pick(tag, ("id", "name", "type", "match", "match_all", "values", "vlan", "org_id"))


def _compact_nac_portal(portal: Any) -> Any:
    if not isinstance(portal, dict):
        return portal
    # Verified against mist_openapi `nac_portal` schema — there is no
    # `enabled`/`auth_type`/`portal_url`/`sso_url` field; the real
    # read-only URLs are `portal_sso_url`, `portal_authorize_url`, `ui_url`.
    return _pick(
        portal,
        ("id", "name", "type", "ssid", "portal_sso_url", "portal_authorize_url", "ui_url", "org_id"),
    )


def _compact_nac_idp(idp: Any) -> Any:
    if not isinstance(idp, dict):
        return idp
    # Verified against mist_openapi `org_setting_mist_nac_idp` schema — this
    # is a realm mapping to an externally-defined identity provider `id`,
    # not a standalone IDP resource with name/type/issuer fields.
    return _pick(idp, ("id", "user_realms", "exclude_realms"))


def _compact_user_mac(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    # Verified against mist_openapi `user_mac` schema — the VLAN field is
    # named `vlan` (string), not `vlan_id`.
    return _pick(
        entry,
        ("id", "mac", "name", "labels", "vlan", "radius_group", "notes"),
    )


def _compact_inventory_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    # Verified against mist_openapi `inventory` schema. Deliberately
    # excludes `magic` — the real field name for the claim code used to add
    # this device (a single-use onboarding secret); there is no `status` or
    # `site_name` field on this schema.
    return _pick(
        item,
        (
            "id",
            "mac",
            "serial",
            "model",
            "type",
            "sku",
            "hw_rev",
            "name",
            "hostname",
            "site_id",
            "org_id",
            "adopted",
            "connected",
            "last_disconnected",
            "vc_mac",
        ),
    )


def _compact_switch(switch: Any) -> Any:
    if not isinstance(switch, dict):
        return switch
    # Verified against mist_openapi `stats_switch` schema — there is no
    # `num_ports` field; per-port detail lives under `ports`/port search.
    return _pick(
        switch,
        (
            "id",
            "mac",
            "name",
            "model",
            "serial",
            "version",
            "status",
            "ip",
            "uptime",
            "site_id",
            "last_seen",
        ),
    )


def _compact_switch_port(port: Any) -> Any:
    if not isinstance(port, dict):
        return port
    # Verified against mist_openapi `searchSiteSwOrGwPorts` response fields.
    return _pick(
        port,
        (
            "port_id",
            "up",
            "full_duplex",
            "speed",
            "poe_disabled",
            "poe_mode",
            "poe_on",
            "mac",
            "neighbor_mac",
            "neighbor_port_desc",
            "neighbor_system_name",
            "stp_state",
            "stp_role",
        ),
    )


def _compact_gateway(gateway: Any) -> Any:
    if not isinstance(gateway, dict):
        return gateway
    # Verified against mist_openapi `stats_gateway` schema — HA/cluster
    # fields are `is_ha`/`cluster_config`, not `ha_config`; `tunnels` and
    # `vpn_peers` carry WAN Edge (SRX/SSR) tunnel/VPN status.
    return _pick(
        gateway,
        (
            "id",
            "mac",
            "name",
            "model",
            "serial",
            "version",
            "status",
            "ip",
            "uptime",
            "is_ha",
            "cluster_config",
            "tunnels",
            "vpn_peers",
            "site_id",
            "last_seen",
        ),
    )


def _compact_marvis_client(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    # Verified against mist_openapi `stats_marvis_client` schema (Marvis
    # Client Android app telemetry) — there is no org-level "list Marvis AI
    # action suggestions" resource in the public spec (only an MSP-scoped
    # count exists at `/msps/{msp_id}/suggestion/count`).
    return _pick(
        entry,
        (
            "device_id",
            "hostname",
            "model",
            "mfg",
            "serial",
            "os_type",
            "os_version",
            "wifi_mac",
            "wifi_ip",
            "wifi_ssid",
            "wifi_rssi",
            "timestamp",
        ),
    )


def _compact_event(event: Any) -> Any:
    if not isinstance(event, dict):
        return event
    # Verified against mist_openapi `device_event` schema.
    return _pick(
        event,
        (
            "type",
            "ev_type",
            "timestamp",
            "site_id",
            "site_name",
            "mac",
            "model",
            "device_name",
            "device_type",
            "ap",
            "ap_name",
            "port_id",
            "text",
            "reason",
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


# ---------------------------------------------------------------------------
# NAC / Access Assurance
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def mist_list_nac_tags(
    org_id: str,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist org NAC tags used by Access Assurance policy rules.

    Uses `GET /api/v1/orgs/{org_id}/nactags`. Mist uses page-based
    pagination, so pass `limit` and `page` to move through larger orgs.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/nactags",
        {"limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["nac_tags"] = bound_collection_response(
            [_compact_nac_tag(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_nac_portals(
    org_id: str,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist org NAC (guest/BYOD) portals.

    Uses `GET /api/v1/orgs/{org_id}/nacportals`. Mist uses page-based
    pagination, so pass `limit` and `page` to move through larger orgs.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/nacportals",
        {"limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["nac_portals"] = bound_collection_response(
            [_compact_nac_portal(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_nac_idps(org_id: str) -> dict[str, Any]:
    """List Mist NAC identity-provider realm mappings backing Access Assurance cloud RADIUS.

    There is no standalone `/nacidps` REST resource in the current Mist
    OpenAPI spec — NAC identity-provider realm mappings live at
    `GET /api/v1/orgs/{org_id}/setting` under `mist_nac.idps` (a list of
    `{id, user_realms, exclude_realms}` entries referencing externally
    defined identity providers). This tool fetches org settings and
    extracts that list.
    """
    out = await _mist_get_request(f"/api/v1/orgs/{_path_segment(org_id)}/setting", bound=False)
    if "data" in out and isinstance(out["data"], dict):
        mist_nac = out["data"].get("mist_nac") or {}
        idps = mist_nac.get("idps") if isinstance(mist_nac, dict) else None
        out["nac_idps"] = bound_collection_response(
            [_compact_nac_idp(item) for item in (idps or [])],
            limit=100,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_user_macs(
    org_id: str,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist org user-MAC entries (known-client MAC-to-label/VLAN mappings).

    Uses `GET /api/v1/orgs/{org_id}/usermacs/search` (there is no GET on the
    `/usermacs` collection root — only `POST`/`PUT`). These entries are
    commonly referenced by NAC rules for static classification (e.g. IoT
    allowlists). Mist uses page-based pagination, so pass `limit`/`page`.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/usermacs/search",
        {"limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["user_macs"] = bound_collection_response(
            [_compact_user_mac(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


# ---------------------------------------------------------------------------
# Marvis AI
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def mist_search_marvis_clients(
    org_id: str,
    hostname: str | None = None,
    model: str | None = None,
    serial: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Search Marvis Client (Android app) telemetry for one Mist org.

    Uses `GET /api/v1/orgs/{org_id}/stats/marvisclients/search`. The public
    Mist OpenAPI spec has no org-level "list Marvis AI action suggestions"
    resource — Marvis Action suggestion data is only exposed as an
    MSP-scoped count (`/api/v1/msps/{msp_id}/suggestion/count`), not a
    listable org resource — so this tool covers verified Marvis *client*
    stats (device/Wi-Fi telemetry from the Marvis mobile app) instead.
    """
    safe_limit = clamp_limit(limit, default=50)
    params = {"hostname": hostname, "model": model, "serial": serial, "limit": safe_limit}
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/stats/marvisclients/search",
        params,
        limit=safe_limit,
        offset=offset,
    )
    if "data" in out:
        out["marvis_clients"] = bound_collection_response(
            [_compact_marvis_client(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=offset,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_get_client_insights(
    site_id: str,
    client_mac: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Get Marvis client experience insights/metrics for one wireless client.

    Uses `GET /api/v1/sites/{site_id}/insights/client/{client_mac}`. Returns
    the Mist-summarized metric series (already compact) bounded to a
    reasonable size; pass `start`/`end` as epoch seconds to scope the window.
    """
    try:
        normalized = _normalize_mac(client_mac)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/insights/client/{normalized}",
        {"start": start, "end": end},
    )
    if "data" in out:
        out["normalized_mac"] = normalized
        out["insights"] = out.pop("data")
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_search_events(
    site_id: str,
    event_type: str | None = None,
    mac: str | None = None,
    model: str | None = None,
    text: str | None = None,
    duration: str = "1d",
    limit: int = 100,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Search recent Mist site device events with compact fields.

    Uses `GET /api/v1/sites/{site_id}/devices/events/search`. There is no
    generic unified `/events/search` endpoint — device, client, NAC-client,
    and other-device events each have their own search path; this tool
    covers device events (AP/switch/gateway). Filter with `event_type`
    (maps to the `type` query param), `mac`, `model`, and/or `text`.
    """
    safe_limit = clamp_limit(limit, default=100)
    params = {
        "type": event_type,
        "mac": mac,
        "model": model,
        "text": text,
        "limit": safe_limit,
        "start": start,
        "end": end,
        "duration": duration,
        "sort": "-timestamp",
    }
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/devices/events/search",
        params,
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["events"] = bound_collection_response(
            [_compact_event(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_get_marvis_settings(org_id: str) -> dict[str, Any]:
    """Get org-level Marvis AI settings from org settings.

    Uses `GET /api/v1/orgs/{org_id}/setting` and returns the nested `marvis`
    object directly (`disable_proactive_monitoring`, `self_driving`) — the
    real `org_setting` schema nests Marvis config under a `marvis` key
    rather than exposing flat top-level `marvis_*`/`vna_*` fields.
    """
    out = await _mist_get_request(f"/api/v1/orgs/{_path_segment(org_id)}/setting", bound=False)
    if "data" in out and isinstance(out["data"], dict):
        out["marvis_settings"] = out["data"].get("marvis") or {}
        del out["data"]
    return out



# ---------------------------------------------------------------------------
# Org inventory and device claims
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def mist_list_org_inventory(
    org_id: str,
    device_type: str | None = None,
    unassigned: bool | None = None,
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List devices in one Mist org's inventory (claimed but possibly unassigned).

    Uses `GET /api/v1/orgs/{org_id}/inventory`. `device_type` maps to Mist's
    `type` filter (`ap`, `switch`, `gateway`). Omits `magic` (the claim code
    used to add the device) from the compact output since it is a
    single-use onboarding secret. Mist uses page-based pagination, so pass
    `limit` and `page`.
    """
    safe_limit = clamp_limit(limit, default=100)
    params = {
        "type": device_type,
        "unassigned": str(unassigned).lower() if unassigned is not None else None,
        "limit": safe_limit,
        "page": max(1, page),
    }
    out = await _mist_get_request(
        f"/api/v1/orgs/{_path_segment(org_id)}/inventory",
        params,
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["inventory"] = bound_collection_response(
            [_compact_inventory_item(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


# ---------------------------------------------------------------------------
# Wired Assurance
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def mist_list_switches(
    site_id: str,
    status: str = "all",
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist site switches with compact status/version/uptime fields.

    Uses `GET /api/v1/sites/{site_id}/stats/devices?type=switch` — Mist has
    no separate `/stats/switches` endpoint; all device types share the
    unified `stats/devices` resource filtered by `type`. `status` maps to
    Mist's `all`/`connected`/`disconnected` filter. Mist uses page-based
    pagination, so pass `limit` and `page` to move through larger sites.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/stats/devices",
        {"type": "switch", "status": status, "limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["switches"] = bound_collection_response(
            [_compact_switch(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_list_switch_ports(
    site_id: str,
    switch_mac: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List port stats for one Mist switch with compact link/PoE fields.

    Uses `GET /api/v1/sites/{site_id}/stats/ports/search` filtered by the
    switch's `mac` and `device_type=switch` — Mist has no nested
    `/stats/switches/{device_id}/ports` path; port search is a unified,
    site-wide resource covering both switch and gateway ports and supports
    `limit`/`sort`/`search_after` pagination (no `page`/`offset`).
    """
    try:
        normalized = _normalize_mac(switch_mac)
    except ValueError as exc:
        return {"error": str(exc)}
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/stats/ports/search",
        {"mac": normalized, "device_type": "switch", "limit": clamp_limit(limit, default=100)},
        bound=False,
    )
    if "data" in out:
        out["normalized_mac"] = normalized
        out["ports"] = bound_collection_response(
            [_compact_switch_port(item) for item in _extract_items(out["data"])],
            limit=limit,
            offset=offset,
        )
        del out["data"]
    return out


# ---------------------------------------------------------------------------
# WAN Assurance (gateways: SRX and SSR)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def mist_list_gateways(
    site_id: str,
    status: str = "all",
    limit: int = 100,
    page: int = 1,
) -> dict[str, Any]:
    """List Mist site WAN Edge gateways (SRX or SSR) with compact status fields.

    Uses `GET /api/v1/sites/{site_id}/stats/devices?type=gateway` — Mist has
    no separate `/stats/gateways` endpoint; all device types share the
    unified `stats/devices` resource filtered by `type`. Mist represents
    both SRX and Session Smart Router (SSR) WAN Edge devices as the
    `gateway` device type — the `model` field distinguishes them; no
    separate `/srx` or `/ssr` REST namespace exists. `is_ha`/`cluster_config`
    and `tunnels`/`vpn_peers` carry HA and WAN tunnel status. Mist uses
    page-based pagination, so pass `limit` and `page`.
    """
    safe_limit = clamp_limit(limit, default=100)
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/stats/devices",
        {"type": "gateway", "status": status, "limit": safe_limit, "page": max(1, page)},
        limit=safe_limit,
        offset=0,
    )
    if "data" in out:
        out["gateways"] = bound_collection_response(
            [_compact_gateway(item) for item in _extract_items(out["data"])],
            limit=safe_limit,
            offset=0,
        )
        del out["data"]
    return out


@mcp.tool(annotations=READ_ONLY)
async def mist_get_gateway(site_id: str, device_id: str) -> dict[str, Any]:
    """Get one Mist WAN Edge gateway (SRX or SSR) with compact status fields.

    Uses `GET /api/v1/sites/{site_id}/stats/devices/{device_id}` — the same
    unified per-device endpoint used for any device type. See
    `mist_list_gateways` for the SRX/SSR model-field caveat.
    """
    out = await _mist_get_request(
        f"/api/v1/sites/{_path_segment(site_id)}/stats/devices/{_path_segment(device_id)}"
    )
    if "data" in out:
        out["gateway"] = _compact_gateway(out.pop("data"))
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


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def mist_upsert_user_mac(
    org_id: str,
    mac_address: str,
    labels: list[str] | None = None,
    vlan: str | None = None,
    radius_group: str | None = None,
    notes: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create one Mist org user-MAC entry for NAC classification.

    Uses `POST /api/v1/orgs/{org_id}/usermacs` with `mac` plus optional
    `labels`, `vlan` (Mist's `user_mac` schema stores VLAN as a string, not
    an integer `vlan_id`), `radius_group`, and `notes`. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    try:
        normalized = _normalize_mac(mac_address)
    except ValueError as exc:
        return {"error": str(exc)}
    body: dict[str, Any] = {"mac": normalized}
    if labels is not None:
        body["labels"] = labels
    if vlan is not None:
        body["vlan"] = vlan
    if radius_group is not None:
        body["radius_group"] = radius_group
    if notes is not None:
        body["notes"] = notes
    out = await _mist_write_request(
        "POST",
        f"/api/v1/orgs/{_path_segment(org_id)}/usermacs",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )
    out["normalized_mac"] = normalized
    return out


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def mist_claim_devices(
    org_id: str,
    claim_codes: list[str],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Claim one or more devices into a Mist org's inventory by claim code.

    Uses `POST /api/v1/orgs/{org_id}/inventory` (`addOrgInventory`) with a
    bare JSON array of claim-code strings as the body — there is no
    `{"op": "claim", ...}` wrapper and no `type` query filter on this
    endpoint (device type is inferred from each claim code). Claim codes are
    masked in the preview since they are single-use onboarding secrets.
    Defaults to `dry_run=True`; execution requires `dry_run=False` and
    `confirm=True`.
    """
    if not claim_codes:
        return {"error": "claim_codes must contain at least one claim code."}
    out = await _mist_write_request(
        "POST",
        f"/api/v1/orgs/{_path_segment(org_id)}/inventory",
        body=claim_codes,
        dry_run=dry_run,
        confirm=confirm,
    )
    if isinstance(out.get("json"), list):
        out["json"] = [
            f"...{code[-4:]}" if len(code) > 4 else "****" for code in claim_codes
        ]
    return out


@mcp.tool(annotations=IDEMPOTENT_WRITE)
async def mist_set_marvis_settings(
    org_id: str,
    settings: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update org-level Marvis AI settings.

    Uses `PUT /api/v1/orgs/{org_id}/setting` with `settings` nested under
    the `marvis` key (`org_setting.marvis`, e.g.
    `{"disable_proactive_monitoring": true}`) — the real `org_setting`
    schema requires the full settings object as the PUT body, so this
    wraps `settings` correctly but still risks clobbering unrelated org
    settings if your Mist release does not merge partial PUT bodies;
    confirm against a live instance before relying on this for production
    orgs. Defaults to `dry_run=True`; execution requires `dry_run=False`
    and `confirm=True`.
    """
    return await _mist_write_request(
        "PUT",
        f"/api/v1/orgs/{_path_segment(org_id)}/setting",
        body={"marvis": settings},
        dry_run=dry_run,
        confirm=confirm,
    )


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        SecretTokenizeMiddleware,
        install_middleware,
    )
    from mcp_servers.shared import run_server

    stable_list_tools(mcp)
    install_middleware(
        mcp,
        [
            NullStripMiddleware(),
            RateLimitMiddleware(rate=8.0),
            SecretTokenizeMiddleware(),
        ],
    )
    run_server(mcp)
