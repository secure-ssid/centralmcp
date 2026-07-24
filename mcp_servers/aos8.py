"""ArubaOS 8 MCP server (49 curated + 258 generated OpenAPI tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=aos8

Auth/env (session login is preferred; the legacy static token still works):
  AOS8_BASE_URL              e.g. https://mobility-conductor.example.com
  AOS8_USERNAME               Mobility Conductor/controller login username
  AOS8_PASSWORD               Mobility Conductor/controller login password
  AOS8_CLIENT_IP               optional `client_ip` query param sent at login
  AOS8_SESSION_TTL_SECONDS      cached session lifetime; default 600, max 3600
  AOS8_API_TOKEN               legacy static bearer token (deprecated fallback)

Session flow (`POST /v1/api/login`): the response's `_global_result.UIDARUBA`
is sent as a `UIDARUBA` query parameter on every subsequent request (not a
bearer header), and non-GET requests also carry the `X-CSRF-Token` response
header when the controller returns one. A 401 clears the cached session and
retries once after a fresh login. `aos8_login`/`aos8_logout` manage the
session explicitly; every other tool auto-logs-in on demand.

The documented AOS8 configuration API only exposes `GET` (reads) and `POST`
(writes, with an `_action` field in the request body — there is no native
PUT/PATCH/DELETE). `aos8_write` and the typed `aos8_manage_*` tools enforce
this: only `GET`/`POST` are accepted.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    DIAGNOSTIC,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    bound_collection_response,
    bounded_response_payload,
    clamp_limit,
    get_client,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)
from mcp_servers.shared import (
    platform_write_blocked as _platform_write_blocked,
)
from mcp_servers.shared import (
    platform_writes_allowed as _platform_writes_allowed,
)

mcp = FastMCP("aos8-core")


def optional_product_writes_allowed() -> bool:
    return _platform_writes_allowed("aos8")


def optional_product_write_blocked(tool_name: str) -> dict[str, str]:
    return _platform_write_blocked("aos8", tool_name)


_ALLOWED_METHODS = {"GET", "POST"}
_CONFIG_ACTIONS = {"create": "add", "update": "modify", "delete": "delete"}
_DEFAULT_SESSION_TTL_SECONDS = 600
_MAX_SESSION_TTL_SECONDS = 3600
_SESSION_CACHE: dict[str, dict[str, Any]] = {}
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
_ALARM_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "Date",
    "date",
    "Severity",
    "severity",
    "Category",
    "category",
    "Type",
    "type",
    "Code",
    "code",
    "Description",
    "description",
    "Message",
    "message",
    "Status",
    "status",
)
_AUDIT_TRAIL_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "Date",
    "date",
    "User",
    "user",
    "Username",
    "username",
    "IP Address",
    "ip_address",
    "Command",
    "command",
    "Config Path",
    "config_path",
    "Action",
    "action",
    "Result",
    "result",
    "Message",
    "message",
)
_EVENT_FIELDS = (
    "Time",
    "time",
    "Timestamp",
    "timestamp",
    "Date",
    "date",
    "Type",
    "type",
    "Severity",
    "severity",
    "Category",
    "category",
    "Source",
    "source",
    "Module",
    "module",
    "Event",
    "event",
    "Description",
    "description",
    "Message",
    "message",
)
_MD_HIERARCHY_FIELDS = (
    "Configuration node",
    "configuration_node",
    "Node",
    "node",
    "Name",
    "name",
    "Path",
    "path",
    "Config Path",
    "config_path",
    "Device Type",
    "device_type",
    "Type",
    "type",
    "IP Address",
    "ip_address",
    "Role",
    "role",
    "Status",
    "status",
)
_RF_NEIGHBOR_FIELDS = (
    "AP Name",
    "ap_name",
    "Neighbor AP Name",
    "neighbor_ap_name",
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
    "Type",
    "type",
    "Status",
    "status",
)
_CLUSTER_STATE_FIELDS = (
    "Cluster",
    "cluster",
    "Group",
    "group",
    "Name",
    "name",
    "Controller",
    "controller",
    "Switch IP",
    "switch_ip",
    "IP Address",
    "ip_address",
    "Role",
    "role",
    "State",
    "state",
    "Status",
    "status",
    "Priority",
    "priority",
)
_AP_WIRED_PORT_FIELDS = (
    "AP Name",
    "ap_name",
    "Port",
    "port",
    "Interface",
    "interface",
    "Status",
    "status",
    "State",
    "state",
    "Mode",
    "mode",
    "VLAN",
    "vlan",
    "Native VLAN",
    "native_vlan",
    "Speed",
    "speed",
    "Duplex",
    "duplex",
    "PoE",
    "poe",
)
_IPSEC_TUNNEL_FIELDS = (
    "Peer",
    "peer",
    "Peer IP",
    "peer_ip",
    "Local IP",
    "local_ip",
    "Remote IP",
    "remote_ip",
    "Tunnel",
    "tunnel",
    "Tunnel ID",
    "tunnel_id",
    "SPI",
    "spi",
    "State",
    "state",
    "Status",
    "status",
    "Uptime",
    "uptime",
    "Packets",
    "packets",
    "Bytes",
    "bytes",
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
    "ssid-prof",
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


def _aos8_session_env() -> dict[str, str | None]:
    """Session-login credentials/settings. Read fresh every call (env can change in tests)."""
    import os

    return {
        "username": os.getenv("AOS8_USERNAME", "").strip() or None,
        "password": os.getenv("AOS8_PASSWORD", "").strip() or None,
        "client_ip": os.getenv("AOS8_CLIENT_IP", "").strip() or None,
        "session_ttl": os.getenv("AOS8_SESSION_TTL_SECONDS", "").strip() or None,
    }


def _aos8_auth_mode() -> str:
    """`session` (preferred) > `legacy_static_token` > `unconfigured`."""
    session_env = _aos8_session_env()
    if session_env["username"] and session_env["password"]:
        return "session"
    _, token = _aos8_config()
    if token:
        return "legacy_static_token"
    return "unconfigured"


def _aos8_session_ttl_seconds(session_env: dict[str, str | None]) -> int:
    raw = session_env.get("session_ttl")
    try:
        ttl = int(raw) if raw else _DEFAULT_SESSION_TTL_SECONDS
    except (TypeError, ValueError):
        ttl = _DEFAULT_SESSION_TTL_SECONDS
    return max(60, min(ttl, _MAX_SESSION_TTL_SECONDS))


async def _aos8_session_login(base_url: str) -> dict[str, Any]:
    """POST /v1/api/login and cache the returned UIDARUBA + X-CSRF-Token.

    AOS8/Mobility Conductor auth is session-based: the login response's
    `_global_result.UIDARUBA` is a session ID sent as a query parameter (not
    a bearer header) on every subsequent request.
    """
    session_env = _aos8_session_env()
    username, password = session_env["username"], session_env["password"]
    if not username or not password:
        return {"error": "AOS8 session login requires AOS8_USERNAME and AOS8_PASSWORD."}

    login_form: dict[str, Any] = {"username": username, "password": password}
    if session_env["client_ip"]:
        login_form["client_ip"] = session_env["client_ip"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{base_url}/v1/api/login", data=login_form)
    except httpx.HTTPError as exc:
        return {"error": f"AOS8 login request failed: {exc}"}

    payload = response_payload(resp)
    global_result = payload.get("_global_result", {}) if isinstance(payload, dict) else {}
    uidaruba = global_result.get("UIDARUBA")
    if resp.status_code >= 400 or not uidaruba:
        return {
            "error": (
                f"AOS8 login failed with HTTP {resp.status_code}: "
                f"{redact_sensitive(payload)}"
            )
        }

    ttl = _aos8_session_ttl_seconds(session_env)
    now = time.time()
    csrf_token = global_result.get("X-CSRF-Token") or resp.headers.get("X-CSRF-Token")
    session_cookie = None
    cookies = getattr(resp, "cookies", None)
    if cookies is not None:
        session_value = cookies.get("SESSION")
        if session_value:
            session_cookie = f"SESSION={session_value}"
    if not session_cookie:
        set_cookie = resp.headers.get("set-cookie", "")
        if set_cookie:
            session_cookie = set_cookie.split(";", 1)[0]
    _SESSION_CACHE[base_url] = {
        "uidaruba": uidaruba,
        "csrf_token": csrf_token,
        "session_cookie": session_cookie,
        "logged_in_at": now,
        "expires_at": now + ttl,
    }
    return {"status": "logged_in", "status_str": global_result.get("status_str")}


async def _aos8_ensure_session(base_url: str) -> dict[str, Any] | None:
    """Return a login error dict if a fresh login is needed and fails; else None."""
    entry = _SESSION_CACHE.get(base_url)
    if entry and entry["expires_at"] > time.time():
        return None
    result = await _aos8_session_login(base_url)
    return result if "error" in result else None


def _aos8_logout_session(base_url: str) -> dict[str, Any]:
    entry = _SESSION_CACHE.pop(base_url, None)
    return {"status": "logged_out" if entry else "no_active_session"}


async def _aos8_dispatch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
    body: Any,
) -> httpx.Response:
    """Call `.get()` for GET (matches every other product backend's read path) or
    `.request()` otherwise, so existing fakes that only implement `.get()` for
    read-only tools keep working unchanged.
    """
    if method == "GET":
        return await client.get(url, headers=headers, params=params)
    return await client.request(method, url, headers=headers, params=params, json=body)


async def _aos8_send(
    method: str,
    base_url: str,
    path: str,
    params: dict[str, Any] | None,
    body: Any,
) -> httpx.Response | dict[str, Any]:
    """Send one authenticated AOS8 request.

    Session mode adds `UIDARUBA` to the query string and `X-CSRF-Token` to
    non-GET requests, auto-logging-in on demand and retrying once on a 401
    (stale/expired session). Legacy mode sends a static `Authorization:
    Bearer <token>` header, unchanged from the original implementation.
    Returns an `httpx.Response` on success, or `{"error": ...}` if the
    request could not be attempted (misconfigured, login failure, or a
    connection/protocol error).
    """
    mode = _aos8_auth_mode()
    if mode == "unconfigured":
        return {
            "error": "AOS8 not configured. Set AOS8_USERNAME/AOS8_PASSWORD (preferred) "
            "or AOS8_BASE_URL and AOS8_API_TOKEN."
        }

    headers = {"Accept": "application/json"}
    req_params = dict(params or {})

    if mode == "session":
        login_error = await _aos8_ensure_session(base_url)
        if login_error is not None:
            return login_error
        entry = _SESSION_CACHE[base_url]
        req_params["UIDARUBA"] = entry["uidaruba"]
        if entry.get("csrf_token"):
            headers["X-CSRF-Token"] = entry["csrf_token"]
        if entry.get("session_cookie"):
            headers["Cookie"] = entry["session_cookie"]
    else:
        _, token = _aos8_config()
        headers["Authorization"] = "Bearer " + (token or "")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await _aos8_dispatch(
                client, method, f"{base_url}{path}", headers, req_params, body
            )
    except httpx.HTTPError as exc:
        return {"error": str(exc)}

    if mode == "session" and resp.status_code == 401:
        _SESSION_CACHE.pop(base_url, None)
        login_error = await _aos8_session_login(base_url)
        if "error" in login_error:
            return login_error
        entry = _SESSION_CACHE[base_url]
        req_params["UIDARUBA"] = entry["uidaruba"]
        if entry.get("csrf_token"):
            headers["X-CSRF-Token"] = entry["csrf_token"]
        if entry.get("session_cookie"):
            headers["Cookie"] = entry["session_cookie"]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await _aos8_dispatch(
                    client, method, f"{base_url}{path}", headers, req_params, body
                )
        except httpx.HTTPError as exc:
            return {"error": str(exc)}

    return resp


def _strip_aos8_envelope(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    out = {key: value for key, value in data.items() if key not in {"_meta", "_global_result"}}
    payload = out.get("_data")
    if len(out) == 1 and isinstance(payload, (dict, list)):
        return payload
    return out


def _aos8_application_error(resp: Any, url: str) -> dict[str, Any] | None:
    payload = response_payload(resp)
    if not isinstance(payload, dict):
        return None
    global_result = payload.get("_global_result")
    if not isinstance(global_result, dict):
        return None
    status = global_result.get("status")
    if status in (None, 0, "0", "Success", "success"):
        return None
    return {
        "error": (
            f"AOS8 API returned application status {status}: "
            f"{global_result.get('status_str') or global_result.get('message') or 'request failed'}"
        ),
        "status_code": resp.status_code,
        "data": redact_sensitive(bounded_response_payload(resp)),
        "url": url,
    }


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
    if method not in _ALLOWED_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_ALLOWED_METHODS))}"}

    base_url, _ = _aos8_config()
    if not base_url or _aos8_auth_mode() == "unconfigured":
        return {
            "error": "AOS8 not configured. Set AOS8_USERNAME/AOS8_PASSWORD (preferred) "
            "or AOS8_BASE_URL and AOS8_API_TOKEN."
        }
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

    result = await _aos8_send(method, base_url, safe_path, params, body)
    if isinstance(result, dict):
        return {**result, "url": url}
    resp = result
    return {
        "status_code": resp.status_code,
        "data": redact_sensitive(response_payload(resp)),
        "url": url,
    }


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


def _extract_primary_list(value: Any) -> list[Any]:
    """Best-effort extraction of the primary record list from a compacted read result."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return value["items"]
        list_values = [v for v in value.values() if isinstance(v, list)]
        if list_values:
            return max(list_values, key=len)
    return []


@mcp.tool(annotations=READ_ONLY)
def aos8_status() -> dict[str, Any]:
    """Report whether the AOS8 backend is configured and the current session state."""
    base_url, token = _aos8_config()
    session_env = _aos8_session_env()
    mode = _aos8_auth_mode()
    entry = _SESSION_CACHE.get(base_url or "")
    now = time.time()
    return {
        "configured": mode != "unconfigured" and bool(base_url),
        "base_url": base_url,
        # Legacy fields kept for backward compatibility with existing callers.
        "has_token": bool(token),
        "auth_mode": mode,
        "has_username": bool(session_env["username"]),
        "has_password": bool(session_env["password"]),
        "has_legacy_token": bool(token),
        "session_active": bool(entry and entry["expires_at"] > now),
        "session_age_seconds": (now - entry["logged_in_at"]) if entry else None,
        "has_csrf_token": bool(entry and entry.get("csrf_token")),
        "allowed_methods": sorted(_ALLOWED_METHODS),
    }


@mcp.tool(annotations=DIAGNOSTIC)
async def aos8_login(force: bool = False) -> dict[str, Any]:
    """Log in to AOS8/Mobility Conductor and cache the session (UIDARUBA + CSRF token).

    Requires `AOS8_USERNAME`/`AOS8_PASSWORD`. No-op if a valid cached session
    already exists unless `force=True`. All other AOS8 tools auto-login on
    demand, so calling this explicitly is only needed to pre-warm or force a
    fresh session.
    """
    base_url, _ = _aos8_config()
    if not base_url:
        return {"error": "AOS8 not configured. Set AOS8_BASE_URL."}
    try:
        base_url = validate_product_base_url(base_url, product="AOS8")
    except ValueError as exc:
        return {"error": str(exc)}
    if _aos8_auth_mode() != "session":
        return {"error": "AOS8 session login requires AOS8_USERNAME and AOS8_PASSWORD."}
    if force:
        _SESSION_CACHE.pop(base_url, None)
    entry = _SESSION_CACHE.get(base_url)
    if entry and entry["expires_at"] > time.time():
        return {
            "status": "already_logged_in",
            "session_age_seconds": time.time() - entry["logged_in_at"],
        }
    return await _aos8_session_login(base_url)


@mcp.tool(annotations=DIAGNOSTIC)
async def aos8_logout() -> dict[str, Any]:
    """Log out of AOS8/Mobility Conductor and clear the cached session, if any."""
    base_url, _ = _aos8_config()
    if not base_url:
        return {"error": "AOS8 not configured. Set AOS8_BASE_URL."}
    base_url = base_url.rstrip("/")
    entry = _SESSION_CACHE.get(base_url)
    if entry:
        headers = {"Accept": "application/json"}
        if entry.get("csrf_token"):
            headers["X-CSRF-Token"] = entry["csrf_token"]
        if entry.get("session_cookie"):
            headers["Cookie"] = entry["session_cookie"]
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(
                    f"{base_url}/v1/api/logout",
                    headers=headers,
                    params={"UIDARUBA": entry["uidaruba"]},
                )
        except httpx.HTTPError as exc:
            result = _aos8_logout_session(base_url)
            return {**result, "warning": f"AOS8 logout request failed: {exc}"}
    return _aos8_logout_session(base_url)


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
    base_url, _ = _aos8_config()
    if not base_url or _aos8_auth_mode() == "unconfigured":
        return {
            "error": "AOS8 not configured. Set AOS8_USERNAME/AOS8_PASSWORD (preferred) "
            "or AOS8_BASE_URL and AOS8_API_TOKEN."
        }
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
    result = await _aos8_send("GET", base_url, path, params, None)
    if isinstance(result, dict):
        return {**result, "url": url}
    resp = result
    payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
    return {"status_code": resp.status_code, "data": payload, "url": url}


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
        out["active_aps"] = _compact_primary_list(
            out.pop("data"),
            _AP_FIELDS,
            limit=limit,
            offset=offset,
        )
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
        out["client"] = _compact_primary_list(
            out.pop("data"),
            _CLIENT_FIELDS,
            limit=limit,
            offset=offset,
        )
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
async def aos8_get_alarms(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List active AOS8 alarms from `show alarms`."""
    out = await aos8_show_command(
        "show alarms",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["alarms"] = _compact_primary_list(
            out.pop("data"),
            _ALARM_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_audit_trail(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Get AOS8 controller-wide audit trail from `show audit-trail`."""
    out = await aos8_show_command("show audit-trail", limit=limit, offset=offset)
    if "data" in out:
        out["audit_trail"] = _compact_primary_list(
            out.pop("data"),
            _AUDIT_TRAIL_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_events(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get recent AOS8 events from `show events`."""
    out = await aos8_show_command(
        "show events",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["events"] = _compact_primary_list(
            out.pop("data"),
            _EVENT_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_md_hierarchy(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Get Mobility Conductor hierarchy from `show configuration node-hierarchy`."""
    out = await aos8_show_command(
        "show configuration node-hierarchy",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["md_hierarchy"] = _compact_primary_list(
            out.pop("data"),
            _MD_HIERARCHY_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_rf_neighbors(
    ap_name: str,
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get ARM RF neighbors for an AP by name."""
    normalized_ap = ap_name.strip()
    if not normalized_ap:
        return {"error": "ap_name is required."}
    out = await aos8_show_command(
        f"show ap arm-neighbors ap-name {normalized_ap}",
        config_path=config_path,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["rf_neighbors"] = _compact_primary_list(
            out.pop("data"),
            _RF_NEIGHBOR_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["ap_name"] = normalized_ap
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_cluster_state(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Get AOS8 LC-cluster membership and failover state."""
    out = await aos8_show_command(
        "show lc-cluster group-membership",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["cluster_state"] = _compact_primary_list(
            out.pop("data"),
            _CLUSTER_STATE_FIELDS,
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_ap_wired_ports(
    ap_name: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get wired-port status for one AP from `show ap port status ap-name`."""
    normalized_ap = ap_name.strip()
    if not normalized_ap:
        return {"error": "ap_name is required."}
    out = await aos8_show_command(
        f"show ap port status ap-name {normalized_ap}",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["wired_ports"] = _compact_primary_list(
            out.pop("data"),
            _AP_WIRED_PORT_FIELDS,
            limit=limit,
            offset=offset,
        )
        out["ap_name"] = normalized_ap
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_ipsec_tunnels(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Get site-to-site and Remote AP IPsec tunnel state."""
    out = await aos8_show_command(
        "show crypto ipsec sa",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["ipsec_tunnels"] = _compact_primary_list(
            out.pop("data"),
            _IPSEC_TUNNEL_FIELDS,
            limit=limit,
            offset=offset,
        )
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
    """List virtual AP profile objects at an AOS8 hierarchy node.

    Some AOS8 builds do not expose the canonical `virtual_ap` config object
    and answer only its legacy `wlan_virtual_ap` name instead (secondary,
    same-owner prior art, not an authoritative API contract:
    https://github.com/secure-ssid/aos8-migration-tool/blob/7bfa884d8e8f1c7e97a7bfa42f15596aa42fcf79/lib/aos8_client.py#L315-L399).
    When the primary lookup fails, this falls back to the legacy name before
    giving up; a failure on both names is reported the same way a single
    failed lookup always has been.
    """
    out = await aos8_get(
        "/v1/configuration/object/virtual_ap",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if _aos8_read_failed(out):
        fallback = await aos8_get(
            "/v1/configuration/object/wlan_virtual_ap",
            {"config_path": config_path},
            limit=limit,
            offset=offset,
        )
        if not _aos8_read_failed(fallback):
            out = fallback
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


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_vlans(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List VLAN configuration objects at an AOS8 hierarchy node."""
    out = await aos8_get(
        "/v1/configuration/object/vlan_id",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["vlans"] = _compact_aos8_data(out.pop("data"), limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


@mcp.tool(annotations=READ_ONLY)
async def aos8_get_policies(
    config_path: str = "/md",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List session-ACL ("policy") configuration objects at an AOS8 hierarchy node.

    Uses the AOS8 `acl_sess` config object. Object-name support can vary by
    controller version; an unexpected or empty response usually means this
    object type is unavailable on the target AOS8 release — verify against
    a live controller before relying on this in a migration run.
    """
    out = await aos8_get(
        "/v1/configuration/object/acl_sess",
        {"config_path": config_path},
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["policies"] = _compact_aos8_data(out.pop("data"), limit=limit, offset=offset)
        out["config_path"] = config_path
    return out


def _aos8_read_failed(out: dict[str, Any]) -> str | None:
    """Return a short failure reason if a read-tool result failed, else None."""
    if "error" in out:
        return str(out["error"])
    status_code = out.get("status_code")
    if isinstance(status_code, int) and not 200 <= status_code < 300:
        return f"HTTP {status_code}"
    return None


async def _aos8_collect_all(
    label: str,
    fetch_page: Callable[[int, int], Awaitable[dict[str, Any]]],
    *,
    page_size: int,
    max_items: int,
) -> tuple[list[Any], list[str]]:
    """Collect locally paged AOS8 results without truncating migration exports."""
    items: list[Any] = []
    warnings: list[str] = []
    offset = 0
    while len(items) < max_items:
        out = await fetch_page(min(page_size, max_items - len(items)), offset)
        failure = _aos8_read_failed(out)
        if failure:
            warnings.append(f"{label}: {failure}")
            break
        collection = out.get(label)
        page = _extract_primary_list(collection)
        collection_is_valid = isinstance(collection, list) or (
            isinstance(collection, dict)
            and any(isinstance(value, list) for value in collection.values())
        )
        if not collection_is_valid:
            warnings.append(
                f"{label}: response collection was missing or malformed"
            )
            break
        items.extend(page)
        if len(page) < min(page_size, max_items - offset):
            break
        offset += len(page)
        if not page:
            break
    if len(items) >= max_items:
        warnings.append(f"{label}: export reached max_items={max_items}")
    return items[:max_items], warnings


async def _aos8_collect_object(
    object_name: str,
    config_path: str,
    *,
    page_size: int,
    max_items: int,
) -> tuple[list[Any], list[str]]:
    async def fetch(limit: int, offset: int) -> dict[str, Any]:
        out = await aos8_get(
            f"/v1/configuration/object/{object_name}",
            {"config_path": config_path},
            limit=limit,
            offset=offset,
        )
        if "data" in out:
            out[object_name] = _compact_aos8_data(
                out.pop("data"), limit=limit, offset=offset
            )
        return out

    return await _aos8_collect_all(
        object_name,
        fetch,
        page_size=page_size,
        max_items=max_items,
    )


@mcp.tool(annotations=READ_ONLY)
async def aos8_export_wlans(
    config_path: str = "/md",
    limit: int = 200,
    max_items: int = 5000,
) -> dict[str, Any]:
    """Export AOS8 WLANs as merged SSID-profile + virtual-AP records for migration planning."""
    page_size = min(clamp_limit(limit, default=200), 200)
    ssid_profiles, ssid_warnings = await _aos8_collect_all(
        "ssid_profiles",
        lambda size, offset: aos8_list_ssid_profiles(
            config_path=config_path, limit=size, offset=offset
        ),
        page_size=page_size,
        max_items=max(1, min(max_items, 20000)),
    )
    virtual_aps, vap_warnings = await _aos8_collect_all(
        "virtual_aps",
        lambda size, offset: aos8_list_virtual_aps(
            config_path=config_path, limit=size, offset=offset
        ),
        page_size=page_size,
        max_items=max(1, min(max_items, 20000)),
    )
    result: dict[str, Any] = {
        "config_path": config_path,
        "ssid_profiles": ssid_profiles,
        "virtual_aps": virtual_aps,
    }
    warnings = [*ssid_warnings, *vap_warnings]
    if warnings:
        result["warnings"] = warnings
    return result


@mcp.tool(annotations=READ_ONLY)
async def aos8_export_all(
    config_path: str = "/md",
    limit: int = 200,
    max_items_per_type: int = 5000,
) -> dict[str, Any]:
    """Export the AOS8 objects used for Classic/New Central migration planning.

    Fans out to WLANs, roles, VLANs, AP groups, controllers, session ACLs,
    AAA/authentication profiles and servers, IPv4/IPv6 routes, and VRRP.
    A failed or malformed response for any single object type is collected in
    `warnings` instead of aborting the whole export, so a partial export is
    still usable. Feed the result to
    `aos8_migration_plan()` (or `pipeline.aos8_migration.build_migration_plan`
    directly) for a deterministic migration plan.
    """
    warnings: list[str] = []

    page_size = min(clamp_limit(limit, default=200), 200)
    max_items = max(1, min(max_items_per_type, 20000))
    wlans = await aos8_export_wlans(
        config_path=config_path,
        limit=page_size,
        max_items=max_items,
    )
    warnings.extend(wlans.pop("warnings", []))

    async def collect(
        label: str,
        fetch: Callable[[int, int], Awaitable[dict[str, Any]]],
    ) -> list[Any]:
        items, item_warnings = await _aos8_collect_all(
            label,
            fetch,
            page_size=page_size,
            max_items=max_items,
        )
        warnings.extend(item_warnings)
        return items

    roles = await collect(
        "user_roles",
        lambda size, offset: aos8_list_user_roles(
            config_path=config_path, limit=size, offset=offset
        ),
    )
    vlans = await collect(
        "vlans",
        lambda size, offset: aos8_get_vlans(
            config_path=config_path, limit=size, offset=offset
        ),
    )
    ap_groups = await collect(
        "ap_groups",
        lambda size, offset: aos8_list_ap_groups(
            config_path=config_path, limit=size, offset=offset
        ),
    )
    controllers = await collect(
        "controllers",
        lambda size, offset: aos8_list_controllers(limit=size, offset=offset),
    )
    policies = await collect(
        "policies",
        lambda size, offset: aos8_get_policies(
            config_path=config_path, limit=size, offset=offset
        ),
    )
    object_names = {
        "aaa_profiles": "aaa_prof",
        "dot1x_auth_profiles": "dot1x_auth_profile",
        "mac_auth_profiles": "mac_auth_profile",
        "server_groups": "server_group_prof",
        "radius_servers": "rad_server",
        "ldap_servers": "ldap_server",
        "tacacs_servers": "tacacs_server",
        "ipv4_routes": "ip_route",
        "ipv6_routes": "ipv6_route",
        "vrrp": "vrrp",
        "vrrp6": "vrrp6",
    }
    extended: dict[str, list[Any]] = {}
    for label, object_name in object_names.items():
        items, item_warnings = await _aos8_collect_object(
            object_name,
            config_path,
            page_size=page_size,
            max_items=max_items,
        )
        extended[label] = items
        warnings.extend(f"{label}: {warning}" for warning in item_warnings)

    return {
        "config_path": config_path,
        "wlans": {
            "ssid_profiles": wlans.get("ssid_profiles", []),
            "virtual_aps": wlans.get("virtual_aps", []),
        },
        "roles": roles,
        "vlans": vlans,
        "ap_groups": ap_groups,
        "controllers": controllers,
        "policies": policies,
        "aaa": {
            key: extended[key]
            for key in (
                "aaa_profiles",
                "dot1x_auth_profiles",
                "mac_auth_profiles",
                "server_groups",
                "radius_servers",
                "ldap_servers",
                "tacacs_servers",
            )
        },
        "routing": {
            key: extended[key]
            for key in ("ipv4_routes", "ipv6_routes", "vrrp", "vrrp6")
        },
        "warnings": warnings,
    }


@mcp.tool(annotations=READ_ONLY)
async def aos8_migration_plan(config_path: str = "/md", limit: int = 200) -> dict[str, Any]:
    """Build a deterministic Classic Central / New Central migration plan for one AOS8 node.

    Calls `aos8_export_all()` and passes the export to the pure-python
    `pipeline.aos8_migration.build_migration_plan`. Returns explicit
    `candidates` (classic_central/new_central), `warnings` for every
    lossy/unsupported field, a stable `diff` per object, and a read-only
    `verification_plan` (existing tool names only — this never calls another
    MCP tool or writes to a target account itself).
    """
    export = await aos8_export_all(config_path=config_path, limit=limit)
    from pipeline.aos8_migration import build_migration_plan

    return build_migration_plan(export)


def _aos8_migration_candidates(
    target_type: str,
    *,
    migration_plan: dict[str, Any] | None,
    candidates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if (migration_plan is None) == (candidates is None):
        raise ValueError("Provide exactly one of migration_plan or candidates.")
    if candidates is not None:
        return candidates
    planned = migration_plan.get("candidates", {}) if migration_plan else {}
    selected = planned.get(target_type)
    if not isinstance(selected, list):
        raise ValueError(
            f"migration_plan.candidates[{target_type!r}] must be a candidate list."
        )
    return selected


def _aos8_migration_target(
    target_type: str,
    *,
    scope_id: str | None,
    scope_name: str | None,
    persona: str,
    conflict_policy: str,
    cluster_name: str | None,
    cluster_scope_id: str | None,
    gateway_name: str | None,
    gateway_scope_id: str | None,
) -> dict[str, Any]:
    return {
        "type": target_type,
        "scope_id": scope_id,
        "scope_name": scope_name,
        "persona": persona,
        "conflict_policy": conflict_policy,
        "cluster_name": cluster_name,
        "cluster_scope_id": cluster_scope_id,
        "gateway_name": gateway_name,
        "gateway_scope_id": gateway_scope_id,
    }


def _aos8_migration_scope_resolver(context: Any) -> tuple[str, str]:
    from mcp_servers.monitoring import get_global_scope_id, list_scopes

    requested_id = str(context.scope_id).strip() if context.scope_id else None
    requested_name = str(context.scope_name).strip() if context.scope_name else None
    if requested_name and requested_name.casefold() in {
        "global",
        "everywhere",
        "org-wide",
        "all aps",
    }:
        global_scope = get_global_scope_id().get("global_scope_id")
        if not global_scope:
            raise ValueError("Could not resolve the target global scope.")
        return str(global_scope), "Global"
    if not requested_id and not requested_name:
        raise ValueError("Target scope_id or scope_name is required.")
    response = list_scopes(full_list=True)
    items = response.get("items", []) if isinstance(response, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("scope_id") or item.get("scopeId") or item.get("id")
        item_name = (
            item.get("scope_name")
            or item.get("scopeName")
            or item.get("name")
        )
        id_matches = requested_id is None or str(item_id) == requested_id
        name_matches = (
            requested_name is None
            or str(item_name).casefold() == requested_name.casefold()
        )
        if item_id and item_name and id_matches and name_matches:
            return str(item_id), str(item_name)
    raise ValueError(
        f"Target scope was not found (scope_id={requested_id!r}, "
        f"scope_name={requested_name!r})."
    )


def _aos8_migration_persona_validator(context: Any) -> str:
    allowed = {
        "CAMPUS_AP",
        "MOBILITY_GW",
        "BRANCH_GW",
        "ACCESS_SWITCH",
        "AGG_SWITCH",
        "CORE_SWITCH",
    }
    persona = str(context.persona or "").strip().upper()
    if persona not in allowed:
        raise ValueError(f"persona must be one of {sorted(allowed)}")
    return persona


def _aos8_migration_read_invoker(operation: Any) -> Any:
    if operation.invocation == "endpoint":
        return get_client().get(str(operation.endpoint))
    from mcp_servers import config as config_tools
    from mcp_servers import nac as nac_tools

    tools = {
        "get_aaa_profile": nac_tools.get_aaa_profile,
        "get_auth_server": nac_tools.get_auth_server,
        "get_ssid": config_tools.get_ssid,
        "list_roles": config_tools.list_roles,
    }
    tool = tools.get(operation.name)
    if tool is None:
        raise ValueError(f"Unapproved migration read tool {operation.name!r}.")
    return tool(**dict(operation.arguments))


def _aos8_migration_write_invoker(
    operation: Any,
    *,
    confirmation: bool,
) -> Any:
    arguments = dict(operation.arguments)
    dry_run = bool(arguments.get("dry_run", False))
    if not dry_run and not confirmation:
        raise PermissionError("Migration write requires explicit confirmation.")
    if not dry_run and not _platform_writes_allowed("central"):
        raise PermissionError(
            "Central writes are disabled; set CENTRALMCP_CENTRAL_WRITES=1."
        )
    if operation.invocation == "endpoint":
        if dry_run:
            return {
                "dry_run": True,
                "method": operation.method,
                "endpoint": operation.endpoint,
                "payload": redact_sensitive(operation.payload),
            }
        method = str(operation.method or arguments.get("method", "")).upper()
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"Unapproved migration write method {method!r}.")
        response = get_client()._request(
            method,
            safe_api_path(
                str(operation.endpoint),
                ("/network-config/", "/configuration/"),
            ),
            json=arguments.get("data", operation.payload),
        )
        return bounded_response_payload(response)

    from mcp_servers import config as config_tools
    from mcp_servers import nac as nac_tools

    tools = {
        "build_overlay_ssid": config_tools.build_overlay_ssid,
        "build_underlay_ssid": config_tools.build_underlay_ssid,
        "create_aaa_profile": nac_tools.create_aaa_profile,
        "create_auth_server": nac_tools.create_auth_server,
        "create_role": config_tools.create_role,
        "create_vlan": config_tools.create_vlan,
        "update_role": config_tools.update_role,
    }
    tool = tools.get(operation.name)
    if tool is None:
        raise ValueError(f"Unapproved migration write tool {operation.name!r}.")
    return tool(**arguments)


def _aos8_migration_orchestrator() -> Any:
    from pipeline.aos8_migration_orchestrator import (
        AOS8MigrationOrchestrator,
        MigrationRunStore,
    )
    from pipeline.aos8_target_adapters import (
        ClassicCentralAdapter,
        NewCentralAdapter,
        TargetType,
    )

    default_state = Path(__file__).resolve().parents[1] / "state" / "aos8_migrations"
    state_dir = os.environ.get(
        "CENTRALMCP_AOS8_MIGRATION_STATE_DIR",
        str(default_state),
    )

    def adapter_factory(context: Any) -> Any:
        adapter_class = (
            NewCentralAdapter
            if context.target_type is TargetType.NEW_CENTRAL
            else ClassicCentralAdapter
        )
        return adapter_class(
            context,
            scope_resolver=_aos8_migration_scope_resolver,
            persona_validator=_aos8_migration_persona_validator,
            read_invoker=_aos8_migration_read_invoker,
            write_invoker=_aos8_migration_write_invoker,
            writes_enabled=lambda _target: _platform_writes_allowed("central"),
        )

    return AOS8MigrationOrchestrator(MigrationRunStore(state_dir), adapter_factory)


def _aos8_migration_error(exc: Exception) -> dict[str, Any]:
    return {
        "status": "blocked",
        "error": str(redact_sensitive(str(exc))),
        "secrets_persisted": False,
    }


@mcp.tool(annotations=READ_ONLY)
def aos8_preview_migration_run(
    target_type: str,
    migration_plan: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    scope_id: str | None = None,
    scope_name: str | None = None,
    persona: str = "CAMPUS_AP",
    conflict_policy: str = "fail",
    cluster_name: str | None = None,
    cluster_scope_id: str | None = None,
    gateway_name: str | None = None,
    gateway_scope_id: str | None = None,
    selected_candidates: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Preview a bounded, dependency-ordered migration run without persisting it."""
    try:
        selected = _aos8_migration_candidates(
            target_type,
            migration_plan=migration_plan,
            candidates=candidates,
        )
        target = _aos8_migration_target(
            target_type,
            scope_id=scope_id,
            scope_name=scope_name,
            persona=persona,
            conflict_policy=conflict_policy,
            cluster_name=cluster_name,
            cluster_scope_id=cluster_scope_id,
            gateway_name=gateway_name,
            gateway_scope_id=gateway_scope_id,
        )
        return _aos8_migration_orchestrator().preview(
            selected,
            target,
            selected=selected_candidates,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def aos8_create_migration_run(
    target_type: str,
    migration_plan: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    scope_id: str | None = None,
    scope_name: str | None = None,
    persona: str = "CAMPUS_AP",
    conflict_policy: str = "fail",
    cluster_name: str | None = None,
    cluster_scope_id: str | None = None,
    gateway_name: str | None = None,
    gateway_scope_id: str | None = None,
    selected_candidates: list[str] | None = None,
    run_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Create an atomic resumable run from a plan/candidate set; secrets are never stored."""
    try:
        selected = _aos8_migration_candidates(
            target_type,
            migration_plan=migration_plan,
            candidates=candidates,
        )
        target = _aos8_migration_target(
            target_type,
            scope_id=scope_id,
            scope_name=scope_name,
            persona=persona,
            conflict_policy=conflict_policy,
            cluster_name=cluster_name,
            cluster_scope_id=cluster_scope_id,
            gateway_name=gateway_name,
            gateway_scope_id=gateway_scope_id,
        )
        return _aos8_migration_orchestrator().create_run(
            selected,
            target,
            selected=selected_candidates,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


@mcp.tool(annotations=DESTRUCTIVE)
def aos8_apply_migration_run(
    run_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    target_secrets: dict[str, dict[str, str]] | None = None,
    retry_failed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Dry-run, apply, or resume a run; real writes require confirm and prior dry-run."""
    try:
        return _aos8_migration_orchestrator().apply(
            run_id,
            dry_run=dry_run,
            confirmation=confirm,
            target_secrets=target_secrets,
            retry_failed=retry_failed,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


@mcp.tool(annotations=READ_ONLY)
def aos8_get_migration_run(
    run_id: str,
    limit: int = 50,
    offset: int = 0,
    include_details: bool = False,
) -> dict[str, Any]:
    """Get bounded candidate state, results, and verification for one migration run."""
    try:
        return _aos8_migration_orchestrator().get_run(
            run_id,
            limit=limit,
            offset=offset,
            include_details=include_details,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


@mcp.tool(annotations=READ_ONLY)
def aos8_list_migration_runs(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List bounded migration-run summaries, reporting malformed state without crashing."""
    try:
        return _aos8_migration_orchestrator().list_runs(
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


@mcp.tool(annotations=READ_ONLY)
def aos8_verify_migration_run(
    run_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Read target objects and record bounded identity/field verification comparisons."""
    try:
        return _aos8_migration_orchestrator().verify(
            run_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        return _aos8_migration_error(exc)


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

    Allows `GET` and `POST` against `/v1/*` paths on the configured ArubaOS 8
    host — the documented AOS8 configuration API has no native PUT/PATCH/DELETE;
    mutations are POST with an `_action` field in the body. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
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


# ---------------------------------------------------------------------------
# Generated OpenAPI tools (see mcp_servers/openapi_gen). The committed manifest
# at mcp_servers/openapi_gen/manifests/aos8.json is a derived operation manifest
# built from the current ArubaOS 8 JSON API OpenAPI document published on the
# Aruba developer portal (ReadMe). Every generated call flows through
# `_aos8_send`, which preserves the UIDARUBA session query param and the
# X-CSRF-Token header on non-GET requests (session auth is injected server-side
# and is NOT a model-visible argument — the manifest strips UIDARUBA). Reads are
# direct; POST configuration/action tools stay behind the write gate +
# dry-run/confirm, and disruptive actions are annotated DESTRUCTIVE via the
# committed capability overrides. Registration is guarded by
# CENTRALMCP_AOS8_GENERATED_TOOLS (defaults ON when the manifest exists).
# ---------------------------------------------------------------------------

# Committed manifest paths are the bare `/object/...` spec paths; the ArubaOS
# JSON API server base is `/v1/configuration`.
_AOS8_API_BASE = "/v1/configuration"


def _aos8_generated_prepare(path: str) -> tuple[str | None, str | None, str | None]:
    """Return (base_url, full_path, error) for a generated ArubaOS 8 request."""
    base_url, _ = _aos8_config()
    if not base_url or _aos8_auth_mode() == "unconfigured":
        return None, None, (
            "AOS8 not configured. Set AOS8_USERNAME/AOS8_PASSWORD (preferred) "
            "or AOS8_BASE_URL and AOS8_API_TOKEN."
        )
    full_path = f"{_AOS8_API_BASE}{path}"
    try:
        full_path = safe_api_path(full_path, ("/v1/",))
    except ValueError as exc:
        return None, None, f"Invalid path. {exc}"
    try:
        base_url = validate_product_base_url(base_url, product="AOS8")
    except ValueError as exc:
        return None, None, str(exc)
    return base_url, full_path, None


async def _aos8_generated_read(
    method: str,
    path: str,
    query: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Read executor for generated AOS8 tools (GET, bounded, session-authed)."""
    base_url, full_path, error = _aos8_generated_prepare(path)
    if error:
        return {"error": error}
    url = f"{base_url}{full_path}"
    clean_params = {k: v for k, v in query.items() if v is not None}
    result = await _aos8_send(method, base_url, full_path, clean_params, None)
    if isinstance(result, dict):
        return {**redact_sensitive(result), "url": url}
    resp = result
    application_error = _aos8_application_error(resp, url)
    if application_error is not None:
        return application_error
    payload = redact_sensitive(bound_collection_response(
        bounded_response_payload(resp), limit=clamp_limit(None), offset=0
    ))
    return {"status_code": resp.status_code, "data": payload, "url": url}


async def _aos8_generated_write(
    name: str,
    method: str,
    path: str,
    query: dict[str, Any],
    headers: dict[str, str],
    body: Any,
    content_type: str,
    dry_run: bool,
    confirm: bool,
) -> dict[str, Any]:
    """Write executor for generated AOS8 tools (gate + dry-run/confirm).

    The ArubaOS JSON API models every mutation as a POST whose body carries the
    object payload (config-set operations also need an `_action` field). Session
    auth (UIDARUBA + X-CSRF-Token) is injected by `_aos8_send`, not the model.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked(name)
    base_url, full_path, error = _aos8_generated_prepare(path)
    if error:
        return {"error": error}
    url = f"{base_url}{full_path}"
    clean_params = {k: v for k, v in query.items() if v is not None}
    preview: dict[str, Any] = {
        "method": method,
        "path": full_path,
        "url": url,
        "params": redact_sensitive(clean_params),
        "json": redact_sensitive(body),
        "content_type": content_type,
    }
    if dry_run:
        return {"dry_run": True, **preview, "execute_hint": _EXECUTE_HINT}
    if not confirm:
        return {
            "error": "confirm=True is required when dry_run=False.",
            "dry_run": True,
            **preview,
        }
    result = await _aos8_send(method, base_url, full_path, clean_params, body)
    if isinstance(result, dict):
        return {**result, "url": url}
    resp = result
    application_error = _aos8_application_error(resp, url)
    if application_error is not None:
        return application_error
    return {
        "status_code": resp.status_code,
        "data": redact_sensitive(bounded_response_payload(resp)),
        "url": url,
    }


def _register_generated_aos8_tools() -> list[str]:
    """Register generated AOS8 tools at import time, failing on manifest errors."""
    from mcp_servers.openapi_gen.runtime import register_generated_tools

    return register_generated_tools(
        mcp,
        "aos8",
        read_executor=_aos8_generated_read,
        write_executor=_aos8_generated_write,
    )


GENERATED_AOS8_TOOLS = _register_generated_aos8_tools()


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
