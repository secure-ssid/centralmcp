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
_CONFIG_ACTIONS = {"create": "add", "update": "modify", "delete": "delete"}
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
_BSS_FIELDS = (
    "BSSID",
    "bssid",
    "AP Name",
    "ap_name",
    "ESSID",
    "essid",
    "SSID",
    "ssid",
    "Band",
    "band",
    "Channel",
    "channel",
    "Type",
    "type",
    "Status",
    "status",
    "Clients",
    "clients",
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
_CLIENT_DETAIL_FIELDS = (
    "Name",
    "name",
    "User Name",
    "username",
    "MAC Address",
    "mac",
    "IP Address",
    "ip_address",
    "IPv6 Address",
    "ipv6_address",
    "AP Name",
    "ap_name",
    "SSID",
    "ssid",
    "Role",
    "role",
    "Mobility Role",
    "mobility_role",
    "VLAN",
    "vlan",
    "Authentication",
    "authentication",
    "Status",
    "status",
    "Uptime",
    "uptime",
)
_CLIENT_HISTORY_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "AP Name",
    "ap_name",
    "BSSID",
    "bssid",
    "SSID",
    "ssid",
    "Event",
    "event",
    "Reason",
    "reason",
    "Status",
    "status",
)
_SYSTEM_LOG_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "Date",
    "date",
    "Module",
    "module",
    "Severity",
    "severity",
    "Level",
    "level",
    "Message",
    "message",
    "Description",
    "description",
)
_ARM_HISTORY_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "AP Name",
    "ap_name",
    "Radio",
    "radio",
    "Band",
    "band",
    "Channel",
    "channel",
    "Event",
    "event",
    "Reason",
    "reason",
    "Status",
    "status",
)
_MONITOR_STATS_FIELDS = (
    "AP Name",
    "ap_name",
    "BSSID",
    "bssid",
    "SSID",
    "ssid",
    "Radio",
    "radio",
    "Band",
    "band",
    "Channel",
    "channel",
    "RSSI",
    "rssi",
    "SNR",
    "snr",
    "Noise Floor",
    "noise_floor",
    "Utilization",
    "utilization",
    "Clients",
    "clients",
    "Status",
    "status",
)
_CONTROLLER_FIELDS = (
    "Name",
    "name",
    "Switch IP",
    "switch_ip",
    "IP Address",
    "ip_address",
    "Model",
    "model",
    "Type",
    "type",
    "Role",
    "role",
    "Status",
    "status",
    "Version",
    "version",
)
_LICENSE_FIELDS = (
    "Name",
    "name",
    "License",
    "license",
    "Feature",
    "feature",
    "Installed",
    "installed",
    "Used",
    "used",
    "Available",
    "available",
    "Expires",
    "expires",
    "Status",
    "status",
)
_RADIO_FIELDS = (
    "AP Name",
    "ap_name",
    "Radio",
    "radio",
    "Band",
    "band",
    "Channel",
    "channel",
    "EIRP",
    "eirp",
    "Power",
    "power",
    "Noise Floor",
    "noise_floor",
    "Utilization",
    "utilization",
    "Clients",
    "clients",
    "Status",
    "status",
)
_VERSION_FIELDS = (
    "Version",
    "version",
    "ArubaOS Version",
    "aos_version",
    "Build",
    "build",
    "Build Date",
    "build_date",
    "Model",
    "model",
    "Uptime",
    "uptime",
    "Hostname",
    "hostname",
)
_USER_ROLE_FIELDS = (
    "role",
    "name",
    "profile-name",
    "acl",
    "access-list",
    "vlan",
    "captive-portal-profile",
    "bw-contract",
    "status",
)
_VIRTUAL_AP_FIELDS = (
    "profile-name",
    "name",
    "ssid-profile",
    "ssid_prof",
    "aaa-profile",
    "aaa_prof",
    "vlan",
    "forward-mode",
    "forward_mode",
    "opmode",
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
    out = {key: value for key, value in data.items() if key not in {"_meta", "_global_result"}}
    payload = out.get("_data")
    if len(out) == 1 and isinstance(payload, (dict, list)):
        return payload
    return out


def _bounded_show_count(value: int, *, default: int = 100, maximum: int = 200) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(count, maximum))


async def _aos8_write_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    tool_name: str = "aos8_write",
) -> dict[str, Any]:
    if not optional_product_writes_allowed():
        return optional_product_write_blocked(tool_name)

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


def _payload_has_identifier(payload: dict[str, Any], identifier_fields: tuple[str, ...]) -> bool:
    return any(payload.get(field) not in (None, "") for field in identifier_fields)


def _aos8_write_preview(out: dict[str, Any]) -> bool:
    return out.get("dry_run") is True and "error" not in out


def _aos8_write_succeeded(out: dict[str, Any]) -> bool:
    status_code = out.get("status_code")
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        return False
    data = out.get("data")
    if not isinstance(data, dict):
        return True
    global_result = data.get("_global_result")
    if not isinstance(global_result, dict):
        return True
    status = global_result.get("status")
    if status is None:
        return True
    return str(status).strip().lower() in {"0", "ok", "success", "succeeded", "true"}


async def _aos8_manage_config_object(
    *,
    tool_name: str,
    object_name: str,
    identifier_fields: tuple[str, ...],
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool,
    confirm: bool,
) -> dict[str, Any]:
    normalized_action = action.strip().lower()
    api_action = _CONFIG_ACTIONS.get(normalized_action)
    if api_action is None:
        return {"error": "action must be one of: create, update, delete"}
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}
    if not _payload_has_identifier(payload, identifier_fields):
        names = ", ".join(repr(field) for field in identifier_fields)
        return {"error": f"payload must include one of: {names}"}

    body = {object_name: {**payload, "_action": api_action}}
    out = await _aos8_write_request(
        "POST",
        "/v1/configuration/object",
        {"config_path": config_path},
        body,
        dry_run=dry_run,
        confirm=confirm,
        tool_name=tool_name,
    )
    if _aos8_write_preview(out) or _aos8_write_succeeded(out):
        out["requires_write_memory_for"] = [config_path]
    return out


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


def _compact_primary_list(
    data: Any,
    fields: tuple[str, ...],
    *,
    limit: int | None = None,
    offset: int = 0,
) -> Any:
    data = _strip_aos8_envelope(data)
    if limit is not None and (
        isinstance(data, list)
        or (isinstance(data, dict) and "_pagination" not in data)
    ):
        data = bound_collection_response(data, limit=limit, offset=offset)
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
        out["aps"] = _compact_primary_list(out.pop("data"), _AP_FIELDS, limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_active_aps(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List active AOS8 APs from `show ap active` with bounded output."""
    out = await aos8_show_command(
        "show ap active",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["active_aps"] = _compact_primary_list(out.pop("data"), _AP_FIELDS, limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_controllers(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List AOS8 Mobility Conductor controllers from `show switches`."""
    out = await aos8_show_command("show switches", limit=limit, offset=offset)
    if "data" in out:
        out["controllers"] = _compact_primary_list(
            out.pop("data"),
            _CONTROLLER_FIELDS,
            limit=limit,
            offset=offset,
        )
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
        out["clients"] = _compact_primary_list(
            out.pop("data"),
            _CLIENT_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_find_client(
    mac: str | None = None,
    ip: str | None = None,
    username: str | None = None,
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Find one AOS8 client by MAC, IP, or username from `show user-table`."""
    selectors = {
        "mac": (mac or "").strip(),
        "ip": (ip or "").strip(),
        "name": (username or "").strip(),
    }
    selected = [(key, value) for key, value in selectors.items() if value]
    if len(selected) != 1:
        return {"error": "Provide exactly one of mac, ip, or username."}
    selector, value = selected[0]
    out = await aos8_show_command(
        f"show user-table {selector} {value}",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["client"] = _compact_primary_list(out.pop("data"), _CLIENT_FIELDS, limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_client_detail(
    mac: str,
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get verbose AOS8 client detail from `show user-table verbose mac`."""
    normalized_mac = mac.strip()
    if not normalized_mac:
        return {"error": "mac is required."}
    out = await aos8_show_command(
        f"show user-table verbose mac {normalized_mac}",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["client_detail"] = _compact_primary_list(
            out.pop("data"),
            _CLIENT_DETAIL_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_client_history(
    mac: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get AOS8 AP association history for a client MAC."""
    normalized_mac = mac.strip()
    if not normalized_mac:
        return {"error": "mac is required."}
    out = await aos8_show_command(
        f"show ap association history client-mac {normalized_mac}",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["client_history"] = _compact_primary_list(
            out.pop("data"),
            _CLIENT_HISTORY_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_system_logs(
    count: int = 100,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get recent AOS8 system log entries with a capped show-command count."""
    bounded_count = _bounded_show_count(count)
    out = await aos8_show_command(
        f"show log system {bounded_count}",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["system_logs"] = _compact_primary_list(
            out.pop("data"),
            _SYSTEM_LOG_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["count"] = bounded_count
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_ap_arm_history(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get AOS8 Adaptive Radio Management history for AP/radio troubleshooting."""
    out = await aos8_show_command(
        "show ap arm history",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["arm_history"] = _compact_primary_list(
            out.pop("data"),
            _ARM_HISTORY_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_ap_monitor_stats(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get AOS8 AP monitor statistics for RF/debug investigations."""
    out = await aos8_show_command(
        "show ap monitor stats",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["monitor_stats"] = _compact_primary_list(
            out.pop("data"),
            _MONITOR_STATS_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_version(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Get AOS8 Mobility Conductor software version from `show version`."""
    out = await aos8_show_command("show version", limit=limit, offset=offset)
    if "data" in out:
        out["version"] = _compact_primary_list(
            out.pop("data"),
            _VERSION_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_licenses(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List AOS8 Mobility Conductor licenses from `show license`."""
    out = await aos8_show_command("show license", limit=limit, offset=offset)
    if "data" in out:
        out["licenses"] = _compact_primary_list(
            out.pop("data"),
            _LICENSE_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_bss(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List AOS8 BSS table entries from `show ap bss-table`."""
    out = await aos8_show_command(
        "show ap bss-table",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["bss"] = _compact_primary_list(out.pop("data"), _BSS_FIELDS, limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_radio_summary(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get AOS8 AP radio summary from `show ap radio-summary`."""
    out = await aos8_show_command(
        "show ap radio-summary",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["radio_summary"] = _compact_primary_list(
            out.pop("data"),
            _RADIO_FIELDS,
            limit=limit,
            offset=offset,
        )
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


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_virtual_aps(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List virtual AP profile objects at an AOS8 hierarchy node."""
    out = await aos8_get(
        "/v1/configuration/object/virtual_ap",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["virtual_aps"] = _compact_primary_list(
            out.pop("data"),
            _VIRTUAL_AP_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_list_user_roles(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List user-role configuration objects at an AOS8 hierarchy node."""
    out = await aos8_get(
        "/v1/configuration/object/role",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["user_roles"] = _compact_primary_list(
            out.pop("data"),
            _USER_ROLE_FIELDS,
            limit=limit,
            offset=offset,
        )
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
    return await _aos8_write_request(
        method,
        path,
        params,
        body,
        dry_run=dry_run,
        confirm=confirm,
        tool_name="aos8_write",
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_manage_ssid_profile(
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create, update, or delete an AOS8 SSID profile; requires write memory."""
    return await _aos8_manage_config_object(
        tool_name="aos8_manage_ssid_profile",
        object_name="ssid_prof",
        identifier_fields=("profile-name",),
        config_path=config_path,
        action=action,
        payload=payload,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_manage_virtual_ap(
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create, update, or delete an AOS8 virtual AP profile; requires write memory."""
    return await _aos8_manage_config_object(
        tool_name="aos8_manage_virtual_ap",
        object_name="virtual_ap",
        identifier_fields=("profile-name",),
        config_path=config_path,
        action=action,
        payload=payload,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_manage_ap_group(
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create, update, or delete an AOS8 AP group; requires write memory."""
    return await _aos8_manage_config_object(
        tool_name="aos8_manage_ap_group",
        object_name="ap_group",
        identifier_fields=("profile-name",),
        config_path=config_path,
        action=action,
        payload=payload,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_manage_user_role(
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create, update, or delete an AOS8 user role; requires write memory."""
    return await _aos8_manage_config_object(
        tool_name="aos8_manage_user_role",
        object_name="role",
        identifier_fields=("rolename",),
        config_path=config_path,
        action=action,
        payload=payload,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_manage_vlan(
    config_path: str,
    action: str,
    payload: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create, update, or delete an AOS8 VLAN; requires write memory."""
    return await _aos8_manage_config_object(
        tool_name="aos8_manage_vlan",
        object_name="vlan_id",
        identifier_fields=("id",),
        config_path=config_path,
        action=action,
        payload=payload,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def aos8_write_memory(
    config_path: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Persist staged AOS8 configuration for a hierarchy node."""
    out = await _aos8_write_request(
        "POST",
        "/v1/configuration/object/write_memory",
        {"config_path": config_path},
        {},
        dry_run=dry_run,
        confirm=confirm,
        tool_name="aos8_write_memory",
    )
    if out.get("dry_run") or "error" not in out:
        out["config_path"] = config_path
    return out


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
