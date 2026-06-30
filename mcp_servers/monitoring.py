"""MCP server — Aruba Central monitoring and operational health tools (61 tools).

Covers: sites, devices, clients, alerts, events, scopes, inventory,
audit logs, device health/trends, switch ports/VLANs/PoE, AP radios/ports,
SLE metrics, WLANs, gateway clusters, anomaly detection (client flapping,
SSH brute force), site health summary, client roaming history, switch stacking,
rogue APs, AP neighbors, channel utilization, client signal history, air quality,
SSID clients, client location.
"""
import time
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel

from mcp_servers.shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    bound_collection_response,
    clamp_limit,
    compact_http_error,
    get_client,
    get_mcp_client,
    maybe_bound,
)

mcp = FastMCP("aruba-monitoring")

# ---------------------------------------------------------------------------
# AP reboot reason translation
# ---------------------------------------------------------------------------

_REBOOT_REASON_MAP: dict[str, str] = {
    "UNKNOWN": "Unknown",
    "AP_RELOAD": "Reload",
    "USER_REBOOT": "User reboot",
    "WRITE_ERASE_REBOOT": "Write erase reboot",
    "WRITE_ERASE_ALL_REBOOT": "Write erase all reboot",
    "IMAGE_SYNC_FAILED": "Image sync failed",
    "IMAGE_SYNC_SUCCESSFUL": "Image sync successful",
    "IMAGE_UPGRADE": "Image upgrade successful",
    "IMAGE_DOWNLOAD_FAILURE": "Image download failure",
    "OUT_OF_MEMORY": "Reboot caused by out of memory",
    "DOWN_UPLINK": "Current uplink down, no useable uplink",
    "CONDUCTOR_TO_LOCAL": "Conductor transitioned to local",
    "NETWORK_DISCONNECT_USB_RESET": "Internet connection lost, reset USB modem",
    "NETWORK_DISCONNECT": "Internet connection lost",
    "UNREACHABLE_GATEWAY": "Gateway unreachable",
    "FATAL_EXCEPTION": "Kernel panic: fatal exception",
    "FATAL_EXCEPTION_IN_INTERRUPT": "Kernel panic: fatal exception in interrupt",
    "SOFTLOCKUP": "Kernel panic: softlockup/hung tasks",
    "NTP_SYNC": "System clock too far ahead of NTP sync",
    "BAD_MESH_LINK": "Mesh link bad — rebooting mesh point",
}


def _translate_reboot_reason(device: dict) -> dict:
    """Translate raw reboot reason code to human-readable string in-place."""
    reason = device.get("lastRebootReason") or device.get("last_reboot_reason")
    if reason and reason in _REBOOT_REASON_MAP:
        if "lastRebootReason" in device:
            device["lastRebootReason"] = _REBOOT_REASON_MAP[reason]
        elif "last_reboot_reason" in device:
            device["last_reboot_reason"] = _REBOOT_REASON_MAP[reason]
    return device


# ── Sites ────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_sites(
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return sites with IDs, names, and location fields (paginated)."""
    sites = get_mcp_client().get_sites(limit=clamp_limit(limit), offset=max(0, offset))
    return maybe_bound(sites, limit=limit, offset=offset)


@mcp.tool(annotations=READ_ONLY)
def get_site(name: str) -> dict[str, Any] | None:
    """Find a site by name (case-insensitive). Returns None if not found."""
    name_lower = name.lower()
    page_size = 100
    offset = 0
    for _ in range(50):
        sites = get_mcp_client().get_sites(limit=page_size, offset=offset)
        if not sites:
            break
        for site in sites:
            site_name = site.get("scopeName") or site.get("siteName") or site.get("name", "")
            if site_name.lower() == name_lower:
                return site
        if len(sites) < page_size:
            break
        offset += page_size
    return None


# ── Devices ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_devices(
    device_type: str | None = None,
    site_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]] | dict[str, Any]:
    """List devices, optionally filtered by device_type (SWITCH/AP) or site_id. Server-side paginated."""
    filters: dict[str, Any] = {}
    if device_type:
        filters["deviceType"] = device_type
    if site_id:
        filters["siteId"] = site_id
    off = max(0, offset)
    devices = get_mcp_client().get_devices(
        filters or None, limit=clamp_limit(limit), offset=off
    )
    # Client already returned the paginated slice — pass offset=0 to
    # maybe_bound so it doesn't try to re-slice the already-trimmed
    # result (which would produce misleading pagination metadata).
    # The true offset is reflected in the _pagination block we attach
    # manually when the flag is on.
    # deviceType query param is ignored server-side; apply client-side post-filter.
    if device_type and isinstance(devices, list):
        want = device_type.upper()
        if want == "AP":
            want = "ACCESS_POINT"
        devices = [d for d in devices if want in (d.get("deviceType") or "").upper()]
    # Translate AP reboot reason codes
    if isinstance(devices, list):
        for d in devices:
            if d.get("deviceType", "").upper() in ("AP", "ACCESS_POINT") or "AP" in d.get("deviceType", "").upper():
                _translate_reboot_reason(d)

    wrapped = maybe_bound(devices, limit=limit, offset=0)
    if isinstance(wrapped, dict) and "_pagination" in wrapped:
        wrapped["_pagination"]["offset"] = off
    return wrapped


@mcp.tool(annotations=READ_ONLY)
def find_device(serial_number: str) -> dict[str, Any] | None:
    """Find a single device by serial number. Returns the device record or None."""
    result = get_mcp_client().get_device_by_serial(serial_number)
    if result:
        dt = (result.get("deviceType") or "").upper()
        if "ACCESS_POINT" in dt or dt == "AP":
            _translate_reboot_reason(result)
    return result


# ── Clients ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_clients(
    site_id: str | None = None,
    serial_number: str | None = None,
    ssid: str | None = None,
    connection_type: str | None = None,
    hostname_contains: str | None = None,
    os_contains: str | None = None,
    device_type_contains: str | None = None,
    ssid_contains: str | None = None,
    site_contains: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]] | dict[str, Any]:
    """List connected clients. ALWAYS filter — unfiltered returns all clients.

    Server-side: site_id, serial_number, ssid, connection_type (Wireless/Wired).
    Client-side substring (case-insensitive, prefer for natural-language queries):
    hostname_contains, os_contains, device_type_contains, ssid_contains, site_contains.
    No server-side offset pagination — narrow filters to page.
    """
    clients = get_mcp_client().get_clients(
        site_id=site_id,
        serial_number=serial_number,
        ssid=ssid,
        connection_type=connection_type,
        limit=clamp_limit(limit),
    )

    # Client-side substring filters. Each filter checks multiple possible field
    # names because Central's v1 and v1alpha1 responses differ.
    def _match(client: dict[str, Any], needle: str, fields: tuple[str, ...]) -> bool:
        n = needle.lower()
        for f in fields:
            v = client.get(f)
            if v and n in str(v).lower():
                return True
        return False

    filters: list[tuple[str, tuple[str, ...]]] = []
    if hostname_contains:
        filters.append((hostname_contains, ("hostName", "clientName", "hostname", "name")))
    if os_contains:
        filters.append((os_contains, ("clientOperatingSystem", "osType")))
    if device_type_contains:
        filters.append((device_type_contains, ("connectedDeviceType", "clientFunction", "clientCategory")))
    if ssid_contains:
        filters.append((ssid_contains, ("network", "wlanName", "ssid", "SSID")))
    if site_contains:
        filters.append((site_contains, ("siteName", "site_name", "site", "scopeName")))

    if filters and isinstance(clients, list):
        clients = [c for c in clients if all(_match(c, needle, fields) for needle, fields in filters)]

    return maybe_bound(clients, limit=limit, offset=0)


@mcp.tool(annotations=READ_ONLY)
def find_client(mac_or_ip: str) -> dict[str, Any] | None:
    """Find a connected client by MAC address or IP address."""
    return get_mcp_client().find_client(mac_or_ip)


@mcp.tool(annotations=READ_ONLY)
def get_client_details(mac_address: str) -> dict[str, Any]:
    """Fetch detailed info (usage, bandwidth, auth) for a single client by MAC address."""
    client = get_client()
    errors: list[str] = []
    mac = mac_address.replace(":", "").replace("-", "").lower()

    for endpoint in [
        f"/network-monitoring/v1/clients/{mac_address}",
        f"/network-monitoring/v1/clients/details?macAddress={mac_address}",
        f"/network-monitoring/v1alpha1/clients/{mac}",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            return {"mac_address": mac_address, "details": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"mac_address": mac_address, "details": None, "endpoint_used": None, "errors": errors}


# ── Alerts & Events ───────────────────────────────────────────────────────────

_ALERT_ACTIONS_BASE = "/network-notifications/v1/alerts"
_ALERT_CLEAR_REASONS = {
    "Problem was resolved",
    "False Positive",
    "Insufficient information for troubleshooting",
    "Alert is not important",
    "Other",
}
_ALERT_PRIORITIES = {"Very High", "High", "Medium", "Low", "Very Low"}
_SEARCH_MIN_CHARS = 3
_SEARCH_MAX_CHARS = 128
_ALERT_CLASSIFICATIONS = {
    "severity",
    "status",
    "priority",
    "category",
    "device_type",
    "impacted_devices",
}
_ALERT_CONFIG_SCOPE_TYPES = {"GLOBAL", "SITE", "DEVICE"}


class _ConfirmAction(BaseModel):
    confirm: bool = False


def _require_non_empty_strings(values: list[str], field: str) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    if not cleaned:
        raise ValueError(f"{field} must contain at least one non-empty value")
    return cleaned


def _json_response(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"items": data}


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _items_from_collection(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("items", "scopes", "devices"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


async def _confirm_alert_action(ctx: Context, action: str, keys: list[str]) -> dict[str, Any] | None:
    try:
        result = await ctx.elicit(
            message=f"Confirm alert {action} for {len(keys)} alert key(s): {keys}",
            schema=_ConfirmAction,
        )
    except Exception as exc:
        return {
            "status": "CONFIRMATION_UNAVAILABLE",
            "error": f"client does not support elicitation; operation NOT performed: {exc}",
        }
    if result.action != "accept" or not result.data.confirm:
        return {"status": "CANCELLED", "detail": "user declined confirmation"}
    return None


def _alert_action(action: str, body: dict[str, Any], submitted_message: str) -> dict[str, Any]:
    endpoint = f"{_ALERT_ACTIONS_BASE}/{action}"
    response = get_client()._request("POST", endpoint, json=body)
    if response.status_code not in (200, 201, 202):
        return {"error": compact_http_error(response, endpoint), "endpoint_used": endpoint}
    data = _json_response(response)
    if not data:
        data = {"submitted": True, "message": submitted_message}
    data.setdefault("endpoint_used", endpoint)
    return data

@mcp.tool(annotations=READ_ONLY)
def list_alerts(
    site_id: str | None = None,
    severity: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]] | dict[str, Any]:
    """List active alerts. severity: CRITICAL/MAJOR/MINOR. No server-side offset pagination — narrow filters to page."""
    alerts = get_mcp_client().get_alerts(
        site_id=site_id, severity=severity, limit=clamp_limit(limit)
    )
    return maybe_bound(alerts, limit=limit, offset=0)


@mcp.tool(annotations=READ_ONLY)
def list_active_alerts(
    site_id: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List active alerts from network-notifications/v1/alerts using OData status filter."""
    client = get_client()
    filters = ["status eq 'Active'"]
    if site_id:
        filters.append(f"siteId eq '{_odata_string(site_id)}'")
    if severity:
        filters.append(f"severity eq '{_odata_string(severity)}'")
    params: dict[str, Any] = {
        "limit": clamp_limit(limit),
        "offset": max(0, offset),
        "filter": " and ".join(filters),
        "sort": "severity desc",
    }
    try:
        return client.get("/network-notifications/v1/alerts", params=params)
    except Exception as exc:
        return {"error": str(exc), "endpoint_used": "/network-notifications/v1/alerts"}


@mcp.tool(annotations=READ_ONLY)
def list_alert_classifications(
    classify_by: str = "severity",
    filter: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """List alert classification metadata from network-notifications/v1/alerts/classification."""
    if classify_by not in _ALERT_CLASSIFICATIONS:
        allowed = ", ".join(sorted(_ALERT_CLASSIFICATIONS))
        raise ValueError(f"classify_by must be one of: {allowed}")
    client = get_client()
    params = {"type": classify_by}
    if filter:
        params["filter"] = filter
    if search:
        params["search"] = search
    try:
        return client.get("/network-notifications/v1/alerts/classification", params=params)
    except Exception as exc:
        return {
            "error": str(exc),
            "endpoint_used": "/network-notifications/v1/alerts/classification",
        }


@mcp.tool(annotations=READ_ONLY)
def list_alert_configs(scope_id: str, scope_type: str = "GLOBAL") -> dict[str, Any]:
    """List alert configuration definitions for a Central scope."""
    scope = scope_id.strip()
    scope_kind = scope_type.strip().upper()
    if not scope:
        raise ValueError("scope_id must be a non-empty string")
    if scope_kind not in _ALERT_CONFIG_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALERT_CONFIG_SCOPE_TYPES))
        raise ValueError(f"scope_type must be one of: {allowed}")
    return get_client().get(
        "/network-notifications/v1/alert-config",
        params={"scopeId": scope, "scopeType": scope_kind},
    )


@mcp.tool(annotations=READ_ONLY)
def list_insights(
    filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List Central Insights recommendation-style observations."""
    params: dict[str, Any] = {"limit": clamp_limit(limit, default=100), "offset": max(0, offset)}
    if filter:
        params["filter"] = filter
    return get_client().get("/network-notifications/v1/insights", params=params)


@mcp.tool(annotations=READ_ONLY)
def get_alert_action_status(task_id: str) -> dict[str, Any]:
    """Return async status for clear/defer/reactivate/priority alert actions."""
    task = quote(task_id.strip(), safe="")
    if not task:
        raise ValueError("task_id must be a non-empty string")
    endpoint = f"{_ALERT_ACTIONS_BASE}/async-operations/{task}"
    response = get_client()._request("GET", endpoint)
    if response.status_code not in (200, 201, 202):
        return {"error": compact_http_error(response, endpoint), "endpoint_used": endpoint}
    data = _json_response(response)
    data.setdefault("endpoint_used", endpoint)
    return data


@mcp.tool(annotations=DESTRUCTIVE)
async def clear_alerts(
    ctx: Context,
    keys: list[str],
    reason: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Clear one or more alerts by key. Returns the Central async task payload."""
    alert_keys = _require_non_empty_strings(keys, "keys")
    if reason not in _ALERT_CLEAR_REASONS:
        allowed = ", ".join(sorted(_ALERT_CLEAR_REASONS))
        raise ValueError(f"reason must be one of: {allowed}")
    body: dict[str, Any] = {"keys": alert_keys, "reason": reason}
    if notes:
        body["notes"] = notes
    cancelled = await _confirm_alert_action(ctx, "clear", alert_keys)
    if cancelled:
        return cancelled
    return _alert_action("clear", body, f"Clear request submitted for {len(alert_keys)} alert(s).")


@mcp.tool(annotations=DESTRUCTIVE)
async def defer_alerts(ctx: Context, keys: list[str], defer_until: str) -> dict[str, Any]:
    """Defer one or more alerts until an absolute ISO-8601 timestamp."""
    alert_keys = _require_non_empty_strings(keys, "keys")
    defer_value = defer_until.strip()
    if not defer_value:
        raise ValueError("defer_until must be a non-empty ISO-8601 timestamp")
    body = {"keys": alert_keys, "deferUntil": defer_value}
    cancelled = await _confirm_alert_action(ctx, "defer", alert_keys)
    if cancelled:
        return cancelled
    return _alert_action("defer", body, f"Defer request submitted for {len(alert_keys)} alert(s).")


@mcp.tool(annotations=DESTRUCTIVE)
async def reactivate_alerts(ctx: Context, keys: list[str]) -> dict[str, Any]:
    """Reactivate cleared or deferred alerts by key."""
    alert_keys = _require_non_empty_strings(keys, "keys")
    body = {"keys": alert_keys}
    cancelled = await _confirm_alert_action(ctx, "reactivate", alert_keys)
    if cancelled:
        return cancelled
    return _alert_action(
        "active",
        body,
        f"Reactivate request submitted for {len(alert_keys)} alert(s).",
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def set_alert_priority(ctx: Context, keys: list[str], priority: str) -> dict[str, Any]:
    """Set operator priority for one or more alerts."""
    alert_keys = _require_non_empty_strings(keys, "keys")
    if priority not in _ALERT_PRIORITIES:
        allowed = ", ".join(sorted(_ALERT_PRIORITIES))
        raise ValueError(f"priority must be one of: {allowed}")
    body = {"keys": alert_keys, "priority": priority}
    cancelled = await _confirm_alert_action(ctx, f"set priority to {priority}", alert_keys)
    if cancelled:
        return cancelled
    return _alert_action(
        "priority",
        body,
        f"Priority update submitted for {len(alert_keys)} alert(s).",
    )


@mcp.tool(annotations=READ_ONLY)
def list_events(
    serial_number: str,
    hours: int = 24,
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List events for a device over the past N hours (bounded by default). Auto-resolves device type + site."""
    events = get_mcp_client().get_events(serial_number, hours=hours)
    if full_list:
        return {
            "items": events,
            "_pagination": {
                "offset": 0,
                "limit": len(events),
                "total": len(events),
                "truncated": False,
            },
        }
    return bound_collection_response(events, limit=limit, offset=offset)


@mcp.tool(annotations=READ_ONLY)
def get_events_count(serial_number: str, hours: int = 24) -> dict[str, Any]:
    """Count events for a device over the past N hours (default 24).

    KNOWN ISSUE: events endpoint unstable. Legacy /events/count 404s;
    /event-filters 400s with unknown param shape. Tries both; surfaces errors.
    """
    client = get_client()
    errors: list[str] = []
    now_ms = int(time.time() * 1000)
    params = {
        "serialNumber": serial_number,
        "startTime": now_ms - hours * 3_600_000,
        "endTime": now_ms,
    }
    # Try peer-consensus path first, then legacy fallback.
    for endpoint in (
        "/network-troubleshooting/v1/event-filters",
        "/network-monitoring/v1/events/count",
    ):
        try:
            result = client.get(endpoint, params=params)
            count = result.get("count")
            if count is None:
                items = result.get("items", [])
                count = sum(i.get("count", 0) for i in items) if items else 0
            return {
                "serial_number": serial_number,
                "count": count,
                "endpoint_used": endpoint,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"serial_number": serial_number, "count": 0, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_radios(
    site_id: str | None = None,
    serial_number: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List radios from network-monitoring/v1/radios with optional site/device filters."""
    client = get_client()
    params: dict[str, Any] = {"limit": clamp_limit(limit), "offset": max(0, offset)}
    if site_id:
        params["siteId"] = site_id
    if serial_number:
        params["serialNumber"] = serial_number
    try:
        return client.get("/network-monitoring/v1/radios", params=params)
    except Exception as exc:
        return {"error": str(exc), "endpoint_used": "/network-monitoring/v1/radios"}


@mcp.tool(annotations=READ_ONLY)
def list_gateways(
    site_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List gateway inventory from network-monitoring/v1/gateways."""
    client = get_client()
    params: dict[str, Any] = {"limit": clamp_limit(limit), "offset": max(0, offset)}
    if site_id:
        params["siteId"] = site_id
    try:
        return client.get("/network-monitoring/v1/gateways", params=params)
    except Exception as exc:
        return {"error": str(exc), "endpoint_used": "/network-monitoring/v1/gateways"}


@mcp.tool(annotations=READ_ONLY)
def list_sites_client_health(
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List per-site client health from network-monitoring/v1/sites-client-health."""
    client = get_client()
    params = {"limit": clamp_limit(limit), "offset": max(0, offset)}
    try:
        return client.get("/network-monitoring/v1/sites-client-health", params=params)
    except Exception as exc:
        return {"error": str(exc), "endpoint_used": "/network-monitoring/v1/sites-client-health"}


@mcp.tool(annotations=READ_ONLY)
def get_tenant_health() -> dict[str, Any]:
    """Return tenant-wide device and client health summaries."""
    client = get_client()
    out: dict[str, Any] = {"device_health": None, "client_health": None, "errors": []}
    try:
        out["device_health"] = client.get("/network-monitoring/v1/tenant-device-health")
    except Exception as exc:
        out["errors"].append(f"tenant-device-health: {exc}")
    try:
        out["client_health"] = client.get("/network-monitoring/v1/tenant-client-health")
    except Exception as exc:
        out["errors"].append(f"tenant-client-health: {exc}")
    return out


# ── Scopes ────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_scopes(
    limit: int = 100,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List scopes (org, sites, device groups) with scope_id and scope_name (bounded by default)."""
    client = get_client()
    items: list[dict[str, Any]] = []

    for endpoint in [
        "/network-config/v1/scopes",
        "/network-config/v1alpha1/scopes",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code in (400, 404):
                continue
            if response.status_code not in (200, 201, 202):
                continue
            data = response.json()
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("items", data.get("scopes", []))
            if isinstance(items, list) and items:
                if full_list:
                    return {
                        "items": items,
                        "_pagination": {
                            "offset": 0,
                            "limit": len(items),
                            "total": len(items),
                            "truncated": False,
                        },
                    }
                return bound_collection_response(items, limit=limit, offset=offset)
        except Exception:
            continue

    # Fallback for tenants where /scopes endpoints return 400.
    # Surface site scopes (plus global scope when available) so callers still get usable scope IDs.
    site_scopes: list[dict[str, Any]] = []
    page_size = 100
    off = 0
    for _ in range(50):
        page = get_mcp_client().get_sites(limit=page_size, offset=off)
        if not page:
            break
        site_scopes.extend(page)
        if len(page) < page_size:
            break
        off += page_size
    normalized: list[dict[str, Any]] = []
    for site in site_scopes:
        scope_id = (
            site.get("scopeId")
            or site.get("scope_id")
            or site.get("siteId")
            or site.get("id")
        )
        scope_name = site.get("scopeName") or site.get("scope_name") or site.get("siteName") or site.get("name")
        if scope_id and scope_name:
            normalized.append(
                {
                    "scope_id": str(scope_id),
                    "scope_name": str(scope_name),
                    "scope_type": "SITE",
                }
            )

    try:
        from pipeline.stages.s6_configure import _fetch_global_scope_id

        global_scope_id = _fetch_global_scope_id(client)
        if global_scope_id:
            normalized.insert(
                0,
                {
                    "scope_id": str(global_scope_id),
                    "scope_name": "Global",
                    "scope_type": "GLOBAL",
                },
            )
    except Exception:
        pass

    if full_list:
        return {
            "items": normalized,
            "_pagination": {
                "offset": 0,
                "limit": len(normalized),
                "total": len(normalized),
                "truncated": False,
            },
        }
    return bound_collection_response(normalized, limit=limit, offset=offset)


@mcp.tool(annotations=READ_ONLY)
def get_global_scope_id() -> dict[str, Any]:
    """Return the org-wide global scope_id — use this for 'everywhere'/'all APs' config."""
    from pipeline.stages.s6_configure import _fetch_global_scope_id
    client = get_client()
    errors: list[str] = []
    try:
        scope_id = _fetch_global_scope_id(client)
        return {"global_scope_id": scope_id, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"global_scope_id": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def find_scope(
    query: str,
    scope_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Find scopes by name or ID substring, optionally narrowed by scope_type."""
    needle = query.strip().lower()
    if not needle:
        raise ValueError("query must be a non-empty string")
    wanted_type = scope_type.strip().upper() if scope_type else None
    scopes = _items_from_collection(list_scopes(full_list=True))
    matches: list[dict[str, Any]] = []
    for scope in scopes:
        sid = str(
            scope.get("scope_id")
            or scope.get("scopeId")
            or scope.get("siteId")
            or scope.get("id")
            or ""
        )
        name = str(
            scope.get("scope_name")
            or scope.get("scopeName")
            or scope.get("siteName")
            or scope.get("name")
            or ""
        )
        kind = str(scope.get("scope_type") or scope.get("scopeType") or scope.get("type") or "")
        if wanted_type and kind.upper() != wanted_type:
            continue
        if needle in sid.lower() or needle in name.lower():
            matches.append(
                {
                    "scope_id": sid,
                    "scope_name": name,
                    "scope_type": kind,
                    "raw": scope,
                }
            )
    return bound_collection_response(matches, limit=limit, offset=0)


@mcp.tool(annotations=READ_ONLY)
def list_scope_devices(
    scope_id: str,
    device_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List devices associated with a site/scope ID using known Central scope fields."""
    scope = scope_id.strip()
    if not scope:
        raise ValueError("scope_id must be a non-empty string")
    page_size = 200
    found: list[dict[str, Any]] = []
    off = 0
    for _ in range(50):
        page = get_mcp_client().get_devices(
            {"siteId": scope},
            limit=page_size,
            offset=off,
        )
        if not page:
            break
        for device in page:
            fields = (
                device.get("scopeId"),
                device.get("scope_id"),
                device.get("siteId"),
                device.get("site_id"),
                device.get("groupId"),
                device.get("deviceGroupId"),
            )
            if scope not in {str(value) for value in fields if value is not None}:
                continue
            if device_type:
                want = device_type.upper()
                raw = str(device.get("deviceType") or device.get("type") or "").upper()
                if want == "AP":
                    want = "ACCESS_POINT"
                if want not in raw:
                    continue
            found.append(device)
        if len(page) < page_size:
            break
        off += page_size
    return bound_collection_response(found, limit=limit, offset=offset)


# ── Inventory ─────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_inventory(
    status: str | None = None,
    device_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List claimed/unprovisioned devices. status: "Yes"=provisioned, "No"=claimed-only. device_type e.g. ACCESS_POINT/SWITCH/GATEWAY."""
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {
        "limit": clamp_limit(limit),
        "offset": max(0, offset),
    }
    if device_type:
        params["deviceType"] = device_type
    try:
        result = client.get("/network-monitoring/v1alpha1/device-inventory", params=params)
        items = result.get("items", result.get("devices", []))
        if not isinstance(items, list):
            items = []
        # deviceType query param is ignored server-side; apply client-side post-filter.
        if device_type:
            want = device_type.upper()
            if want == "AP":
                want = "ACCESS_POINT"
            items = [d for d in items if want in (d.get("deviceType") or "").upper()]
        if status:
            items = [d for d in items if d.get("isProvisioned") == status]
        return {"items": items, "total": len(items), "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "total": 0, "errors": errors}


# ── Audit Logs ────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_audit_logs(
    start_at: int | None = None,
    end_at: int | None = None,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """Audit logs are not available on New Central instances.

    The audit-log endpoint 404s and is absent from all OpenAPI specs. Use
    list_glp_audit_logs (aruba-glp) for GreenLake Platform audit trails instead.
    """
    return {
        "items": [],
        "errors": [
            "audit-log endpoint not available on New Central instances — "
            "use list_glp_audit_logs (aruba-glp) instead"
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def get_audit_log(audit_id: str) -> dict[str, Any]:
    """Audit logs are not available on New Central instances.

    The audit-log endpoint 404s and is absent from all OpenAPI specs. Use
    list_glp_audit_logs (aruba-glp) for GreenLake Platform audit trails instead.
    """
    return {
        "items": [],
        "errors": [
            "audit-log endpoint not available on New Central instances — "
            "use list_glp_audit_logs (aruba-glp) instead"
        ],
    }


# ── Device Health & Trends ────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_device_trends(
    serial_number: str,
    metric: str,
    start_time: str,
    end_time: str,
    site_id: str | None = None,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Time-series utilization trends for an AP or switch.

    metric: cpu/memory/throughput. start_time/end_time: ISO 8601.
    device_type AP/SWITCH auto-detected if omitted.
    """
    client = get_client()
    errors: list[str] = []

    if not device_type:
        device = get_mcp_client().get_device_by_serial(serial_number)
        if device:
            raw = device.get("deviceType", "")
            if "ACCESS_POINT" in raw or raw == "AP":
                device_type = "AP"
            elif "SWITCH" in raw:
                device_type = "SWITCH"
            elif "GATEWAY" in raw:
                device_type = "GATEWAY"

    filter_str = f"timestamp gt {start_time} and timestamp lt {end_time}"
    params: dict[str, Any] = {"filter": filter_str}
    if site_id:
        params["site-id"] = site_id

    dt = (device_type or "").upper()
    m = metric.lower()
    if dt in ("AP", "ACCESS_POINT"):
        metric_segment = "throughput-trends" if m == "throughput" else f"{m}-utilization-trends"
        candidates = [f"/network-monitoring/v1/aps/{serial_number}/{metric_segment}"]
        if m == "throughput":
            params.setdefault("interface-type", "WIRELESS")
    elif dt in ("SWITCH", "CX"):
        if m in ("cpu", "memory", "hardware"):
            metric_segment = "hardware-trends"
        elif m == "throughput":
            metric_segment = "interface-trends"
        else:
            metric_segment = f"{m}-utilization-trends"
        candidates = [
            f"/network-monitoring/v1/switches/{serial_number}/{metric_segment}",
            f"/network-monitoring/v1alpha1/switch/{serial_number}/{metric_segment}",
        ]
    else:
        if m == "throughput":
            candidates = [
                f"/network-monitoring/v1/aps/{serial_number}/throughput-trends",
                f"/network-monitoring/v1/switches/{serial_number}/interface-trends",
            ]
        elif m in ("cpu", "memory", "hardware"):
            candidates = [
                f"/network-monitoring/v1/aps/{serial_number}/{m}-utilization-trends",
                f"/network-monitoring/v1/switches/{serial_number}/hardware-trends",
            ]
        else:
            candidates = [
                f"/network-monitoring/v1/aps/{serial_number}/{m}-utilization-trends",
                f"/network-monitoring/v1/switches/{serial_number}/{m}-utilization-trends",
            ]

    for endpoint in candidates:
        try:
            response = client._request("GET", endpoint, params=params)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            return {"serial_number": serial_number, "metric": metric, "trends": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "metric": metric, "trends": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_device_health(
    serial_number: str | None = None,
    device_scope_id: str | None = None,
) -> dict[str, Any]:
    """Fetch config-health or monitoring health state for a device."""
    client = get_client()
    errors: list[str] = []

    try:
        params: dict[str, Any] = {}
        if device_scope_id:
            params["scope-id"] = device_scope_id
        response = client._request("GET", "/network-config/v1alpha1/config-health/devices", params=params or None)
        if response.status_code == 200:
            data = response.json()
            items = data.get("items", data.get("devices", [data] if data else []))
            if serial_number and isinstance(items, list):
                matches = [i for i in items if (i.get("serial") or i.get("serialNumber") or "").lower() == serial_number.lower()]
                items = matches if matches else items
            return {"serial_number": serial_number, "health": items, "endpoint_used": "/network-config/v1alpha1/config-health/devices", "errors": errors}
        errors.append(f"config-health: HTTP {response.status_code}")
    except Exception as exc:
        errors.append(f"config-health: {exc}")

    if serial_number:
        for endpoint in [
            f"/network-monitoring/v1/devices/{serial_number}",
            f"/network-monitoring/v1alpha1/devices/{serial_number}",
        ]:
            try:
                response = client._request("GET", endpoint)
                if response.status_code == 404:
                    errors.append(f"404 at {endpoint}")
                    continue
                if response.status_code == 200:
                    return {"serial_number": serial_number, "health": response.json(), "endpoint_used": endpoint, "errors": errors}
                errors.append(f"HTTP {response.status_code} at {endpoint}")
            except Exception as exc:
                errors.append(str(exc))

    return {"serial_number": serial_number, "health": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_device_config_issues(serial_number: str) -> dict[str, Any]:
    """Return active configuration issues and recommended actions for one device."""
    serial = serial_number.strip()
    if not serial:
        raise ValueError("serial_number must be a non-empty string")
    endpoint = "/network-config/v1alpha1/config-health/active-issue"
    return get_client().get(endpoint, params={"serial": serial})


@mcp.tool(annotations=READ_ONLY)
def list_devices_config_health(
    limit: int = 100,
    offset: int = 0,
    sort: str | None = None,
    filter: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """List fleet config-health summaries, optionally sorted/filtered/searched."""
    if search is not None and not (_SEARCH_MIN_CHARS <= len(search) <= _SEARCH_MAX_CHARS):
        raise ValueError(
            f"search must be {_SEARCH_MIN_CHARS}-{_SEARCH_MAX_CHARS} characters, got {len(search)}"
        )
    params: dict[str, Any] = {"limit": clamp_limit(limit, default=100), "offset": max(0, offset)}
    if sort:
        params["sort"] = sort
    if filter:
        params["filter"] = filter
    if search:
        params["search"] = search
    return get_client().get("/network-config/v1alpha1/config-health/devices", params=params)


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def resync_device_config(serial_numbers: list[str]) -> dict[str, Any]:
    """Trigger full Central config resync for one or more device serial numbers."""
    serials = _require_non_empty_strings(serial_numbers, "serial_numbers")
    return get_client().post(
        "/network-config/v1alpha1/config-health/devices-resync",
        data={"serials": serials},
    )


@mcp.tool(annotations=READ_ONLY)
def get_wireless_metrics(serial_number: str) -> dict[str, Any]:
    """Fetch AP wireless metrics: RF stats, client count, utilization, channel."""
    client = get_client()
    errors: list[str] = []

    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}",
        f"/network-monitoring/v1/devices/{serial_number}/wireless-stats",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/rf-stats",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            return {"serial_number": serial_number, "metrics": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "metrics": None, "endpoint_used": None, "errors": errors}


# ── Switch Monitoring ─────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_switch_ports(
    serial_number: str,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """List switch interfaces (link state, speed, duplex, VLAN). filter: OData e.g. "speed eq '1000'"."""
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {
        "limit": clamp_limit(limit),
        "offset": max(0, offset),
    }
    if filter:
        params["filter"] = filter
    if search:
        params["search"] = search

    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}/interfaces",
        f"/network-monitoring/v1alpha1/switch/{serial_number}/interfaces",
    ]:
        try:
            response = client._request("GET", endpoint, params=params)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            data = response.json()
            interfaces = data.get("interfaces", data.get("items", data))
            return {"serial_number": serial_number, "interfaces": interfaces, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "interfaces": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_switch_details(serial_number: str) -> dict[str, Any]:
    """Fetch full monitoring details for a switch (status, uptime, CPU, memory, VLANs)."""
    client = get_client()
    errors: list[str] = []

    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}",
        f"/network-monitoring/v1alpha1/switch/{serial_number}",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            return {"serial_number": serial_number, "details": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "details": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_switch_vlans(
    serial_number: str,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List VLANs active on a switch (status, membership). filter: OData e.g. "status in ('Up')"."""
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {
        "limit": clamp_limit(limit),
        "offset": max(0, offset),
    }
    if filter:
        params["filter"] = filter

    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}/vlans",
        f"/network-monitoring/v1alpha1/switch/{serial_number}/vlans",
    ]:
        try:
            response = client._request("GET", endpoint, params=params)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            data = response.json()
            vlans = data.get("vlans", data.get("items", data))
            return {"serial_number": serial_number, "vlans": vlans, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "vlans": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_switch_interface_poe(
    serial_number: str,
    site_id: str | None = None,
) -> dict[str, Any]:
    """Fetch PoE state and power draw for all ports on a switch."""
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {}
    if site_id:
        params["site-id"] = site_id

    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}/interface-poe",
        f"/network-monitoring/v1alpha1/switch/{serial_number}/interface-poe",
    ]:
        try:
            response = client._request("GET", endpoint, params=params or None)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            data = response.json()
            poe = data.get("interfaces", data.get("items", data))
            return {"serial_number": serial_number, "poe": poe, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "poe": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_switch_interface_trends(
    serial_number: str,
    start_time: str,
    end_time: str,
    site_id: str | None = None,
    interface_id: str | None = None,
    uplink: bool | None = None,
) -> dict[str, Any]:
    """Throughput trends for switch interfaces over a time window.

    start_time/end_time ISO 8601. interface_id e.g. "7" or "1/1/6".
    """
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {"filter": f"timestamp gt {start_time} and timestamp lt {end_time}"}
    if site_id:
        params["site-id"] = site_id
    if interface_id:
        params["interface-id"] = interface_id
    if uplink is not None:
        params["uplink"] = str(uplink).lower()

    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}/interface-trends",
        f"/network-monitoring/v1alpha1/switch/{serial_number}/interface-trends",
    ]:
        try:
            response = client._request("GET", endpoint, params=params)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            return {"serial_number": serial_number, "trends": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "trends": None, "endpoint_used": None, "errors": errors}


# ── AP Sub-Resources ──────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_ap_radios(serial_number: str) -> dict[str, Any]:
    """List radios on an AP with band, channel, power, utilization, and mode."""
    client = get_client()
    errors: list[str] = []

    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}/radios",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/radios",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            data = response.json()
            radios = data.get("radios", data.get("items", data))
            return {"serial_number": serial_number, "radios": radios, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "radios": None, "endpoint_used": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_ap_ports(serial_number: str) -> dict[str, Any]:
    """List wired ports on an AP with link state, speed, VLAN, and duplex."""
    client = get_client()
    errors: list[str] = []

    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}/ports",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/ports",
    ]:
        try:
            response = client._request("GET", endpoint)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(f"HTTP {response.status_code} at {endpoint}")
                continue
            data = response.json()
            ports = data.get("ports", data.get("items", data))
            return {"serial_number": serial_number, "ports": ports, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "ports": None, "endpoint_used": None, "errors": errors}


# ── SLE ───────────────────────────────────────────────────────────────────────
#
# get_sle_metrics was removed: neither /network-monitoring/v1/sle nor
# /network-monitoring/v1alpha1/sle nor any sibling variant
# (/service-level, /wireless-service-level, /connectivity/sle) exist in the
# New Central API. No reviewed peer MCP wraps SLE either. Bring it back
# here only when the official API exposes a real path.


# ── WLANs ─────────────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_wlans(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List all WLANs visible in New Central monitoring."""
    client = get_client()
    lim = clamp_limit(limit)
    off = max(0, offset)
    try:
        return client.get(f"/network-monitoring/v1/wlans?limit={lim}&offset={off}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def get_wlan(wlan_name: str) -> dict[str, Any]:
    """Fetch monitoring details for a single WLAN by name."""
    client = get_client()
    try:
        return client.get(f"/network-monitoring/v1/wlans/{wlan_name}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def list_ap_wlans(serial_number: str) -> dict[str, Any]:
    """List WLANs currently active on a specific AP."""
    client = get_client()
    try:
        return client.get(f"/network-monitoring/v1/aps/{serial_number}/wlans")
    except Exception as exc:
        return {"error": str(exc)}


# ── Gateway Clusters ──────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_cluster_members(cluster_name: str) -> dict[str, Any]:
    """List members of a gateway cluster."""
    client = get_client()
    try:
        return client.get(f"/network-monitoring/v1/clusters/{cluster_name}/members")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def get_cluster_tunnels(cluster_name: str) -> dict[str, Any]:
    """List tunnels for a gateway cluster."""
    client = get_client()
    try:
        return client.get(f"/network-monitoring/v1/clusters/{cluster_name}/tunnels")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def get_cluster_tunnel_health(cluster_name: str) -> dict[str, Any]:
    """Get tunnel health summary (up/down counts) for a gateway cluster."""
    client = get_client()
    try:
        return client.get(f"/network-monitoring/v1/clusters/{cluster_name}/tunnels-health-summary")
    except Exception as exc:
        return {"error": str(exc)}


# ── Switch Extended Monitoring ───────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_switch_stacking_info(serial_number: str) -> dict[str, Any]:
    """Get stacking status for a CX switch stack.

    Returns stack members, roles (conductor/standby/member), serial numbers,
    MAC addresses, and forwarding-plane health. Returns a not-applicable
    response for standalone switches. Note: stacking sub-path endpoints
    are not yet exposed in New Central — use get_switch_details which
    includes stackId and switchRole fields.
    """
    client = get_client()
    errors: list[str] = []
    for endpoint in [
        f"/network-monitoring/v1/switches/{serial_number}/stack",
        f"/network-monitoring/v1alpha1/switch/{serial_number}/stack",
        f"/network-monitoring/v1/switches/{serial_number}/stack-members",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            data = resp.json()
            return {"serial_number": serial_number, "stack": data, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"serial_number": serial_number, "stack": None, "errors": errors,
            "_note": "Stack endpoint not found — switch may be standalone or endpoint not yet available"}


# ── Wireless Extended Monitoring ─────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_channel_utilization(serial_number: str) -> dict[str, Any]:
    """Get per-radio channel utilization and noise floor for an AP.

    Returns busy percentage, noise floor (dBm), channel number, and
    interference score for each radio. The first metric to check when
    clients are slow but signal is good.
    """
    client = get_client()
    errors: list[str] = []
    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}/radios",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/rf-stats",
        f"/network-monitoring/v1/aps/{serial_number}/channel-utilization",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            data = resp.json()
            radios = data.get("radios", data.get("items", data if isinstance(data, list) else [data]))
            summary = []
            for r in (radios if isinstance(radios, list) else []):
                summary.append({
                    "band": r.get("band") or r.get("radio_band"),
                    "channel": r.get("channel") or r.get("primary_channel"),
                    "utilization_pct": r.get("utilization") or r.get("channel_utilization"),
                    "noise_floor_dbm": r.get("noise") or r.get("noise_floor"),
                    "tx_power_dbm": r.get("txPower") or r.get("tx_power"),
                    "client_count": r.get("clientCount") or r.get("client_count"),
                })
            return {"serial_number": serial_number, "endpoint_used": endpoint,
                    "radios": summary, "raw": data, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"serial_number": serial_number, "radios": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_rogue_aps(
    site_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List rogue and interfering APs detected by the wireless infrastructure.

    Returns rogue BSSID, SSID, channel, RSSI, classification (rogue/interfering/
    neighbour), and detecting AP. Note: rogue AP endpoints (/rogues, /rogue-aps)
    are not yet exposed in New Central — this tool will return an empty result
    with an explanatory note until the endpoint is available.
    """
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {"limit": clamp_limit(limit)}
    if site_id:
        params["site-id"] = site_id
    for endpoint in [
        "/network-monitoring/v1/rogues",
        "/network-monitoring/v1alpha1/rogues",
        "/network-monitoring/v1/rogue-aps",
    ]:
        try:
            resp = client._request("GET", endpoint, params=params)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            data = resp.json()
            items = data.get("rogues", data.get("items", data if isinstance(data, list) else []))
            return bound_collection_response(items, limit=limit, offset=0)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"items": [], "errors": errors, "_note": "Rogue AP endpoint not found or no rogues detected"}


@mcp.tool(annotations=READ_ONLY)
def get_ap_neighbors(serial_number: str) -> dict[str, Any]:
    """Get neighboring APs visible to this AP with RSSI and channel.

    Returns BSSIDs, SSIDs, channels, and signal strength of APs heard by
    this AP. Useful for coverage overlap analysis and co-channel interference
    identification.
    """
    client = get_client()
    errors: list[str] = []
    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}/neighbors",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/neighbors",
        f"/network-monitoring/v1/aps/{serial_number}/rf-neighbors",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            data = resp.json()
            neighbors = data.get("neighbors", data.get("items", data if isinstance(data, list) else []))
            return {"serial_number": serial_number, "neighbors": neighbors,
                    "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"serial_number": serial_number, "neighbors": None, "errors": errors,
            "_note": "Neighbor endpoint not found — may not be available in New Central yet"}


@mcp.tool(annotations=READ_ONLY)
def get_client_signal_history(
    mac_address: str,
    hours: int = 24,
) -> dict[str, Any]:
    """Get RSSI and SNR history for a wireless client over the past N hours.

    Returns signal strength trends showing whether a client's poor performance
    is due to degrading signal or is intermittent. Note: client sub-path
    endpoints (signal-history, trends) are not yet exposed in New Central.
    Use get_client_roaming_history for event-based connection history instead.
    """
    client = get_client()
    errors: list[str] = []
    mac_clean = mac_address.replace(":", "").replace("-", "").lower()

    for endpoint in [
        f"/network-monitoring/v1/clients/{mac_clean}/signal-history",
        f"/network-monitoring/v1/clients/{mac_address}/signal-history",
        f"/network-monitoring/v1alpha1/clients/{mac_clean}/signal-history",
        f"/network-monitoring/v1/clients/{mac_clean}/trends",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            data = resp.json()
            return {"mac_address": mac_address, "history": data,
                    "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"mac_address": mac_address, "history": None, "errors": errors,
            "_note": "Signal history endpoint not found — use get_client_roaming_history for event-based history"}


@mcp.tool(annotations=READ_ONLY)
def list_ssid_clients(
    ssid_name: str,
    site_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List all clients currently connected to a specific SSID.

    Returns client MAC, IP, hostname, signal, band, and connected AP.
    Useful for per-SSID capacity checks and isolating SSID-specific issues.
    """
    clients = get_mcp_client().get_clients(
        site_id=site_id,
        ssid=ssid_name,
        limit=clamp_limit(limit),
    )
    return bound_collection_response(clients, limit=limit, offset=0)


@mcp.tool(annotations=READ_ONLY)
def locate_client(mac_address: str) -> dict[str, Any]:
    """Get the approximate physical location of a client.

    Returns floor plan coordinates, building, floor, and nearest AP where
    available. Note: location endpoints (/location/v1) require a separate
    location services licence and are not available on all Central instances.
    """
    client = get_client()
    errors: list[str] = []
    mac_clean = mac_address.replace(":", "").replace("-", "").lower()

    for endpoint in [
        f"/network-monitoring/v1/clients/{mac_clean}/location",
        f"/network-monitoring/v1/clients/{mac_address}/location",
        f"/network-monitoring/v1alpha1/clients/{mac_clean}/location",
        f"/location/v1/clients/{mac_clean}",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            return {"mac_address": mac_address, "location": resp.json(),
                    "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"mac_address": mac_address, "location": None, "errors": errors,
            "_note": "Location endpoint not found — location services may require a separate licence"}


@mcp.tool(annotations=READ_ONLY)
def get_air_quality(serial_number: str) -> dict[str, Any]:
    """Get air quality and interference metrics for an AP.

    Returns interference score, non-Wi-Fi interference sources, duty cycle,
    and air quality index per radio. Note: air-quality and rf-health sub-paths
    are not yet exposed in New Central — use get_channel_utilization (AP radios
    endpoint) for available RF metrics in the meantime.
    """
    client = get_client()
    errors: list[str] = []
    for endpoint in [
        f"/network-monitoring/v1/aps/{serial_number}/air-quality",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/air-quality",
        f"/network-monitoring/v1/aps/{serial_number}/rf-health",
    ]:
        try:
            resp = client._request("GET", endpoint)
            if resp.status_code in (400, 404):
                errors.append(f"HTTP {resp.status_code} at {endpoint}")
                continue
            if resp.status_code not in (200, 201, 202):
                errors.append(compact_http_error(resp, endpoint))
                continue
            return {"serial_number": serial_number, "air_quality": resp.json(),
                    "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    return {"serial_number": serial_number, "air_quality": None, "errors": errors,
            "_note": "Air quality endpoint not found — may not be available in New Central yet"}


# ── Client History ───────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def get_client_roaming_history(
    mac_address: str,
    hours: int = 24,
) -> dict[str, Any]:
    """Show where a client has roamed across switches/APs over the past N hours.

    Queries Client Onboarding events across all switches and APs in the
    environment, filtered to the given MAC. Returns a chronological list of
    connections showing which device/port/VLAN the client was seen on and when.
    Useful for tracing connectivity issues that follow a user around.
    """
    client = get_mcp_client()

    devices = client.get_devices(limit=200)
    switches = [d for d in devices if "SWITCH" in (d.get("deviceType") or "").upper()]
    aps = [d for d in devices if d.get("deviceType") in ("AP", "ACCESS_POINT")]

    mac_lower = mac_address.lower()
    history: list[dict[str, Any]] = []

    for device in switches + aps:
        serial = device.get("serialNumber") or device.get("id", "")
        if not serial:
            continue
        events = client.get_events(serial, hours=hours, api_limit=500)
        for e in events:
            if (e.get("clientMacAddress") or "").lower() == mac_lower:
                history.append({
                    "time": e.get("timeAt"),
                    "event": e.get("eventName"),
                    "device_name": device.get("deviceName") or serial,
                    "device_serial": serial,
                    "device_type": device.get("deviceType"),
                    "description": e.get("description"),
                })

    history.sort(key=lambda x: x.get("time") or "", reverse=True)

    return {
        "mac_address": mac_address,
        "hours_analyzed": hours,
        "devices_scanned": len(switches) + len(aps),
        "event_count": len(history),
        "history": history,
    }


# ── Intelligence / Anomaly Detection ─────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def detect_client_flapping(
    serial_number: str,
    hours: int = 24,
    min_events: int = 5,
) -> dict[str, Any]:
    """Detect wired/wireless clients re-onboarding abnormally often on a switch.

    Fetches Client Onboarding events for the device over the past N hours and
    flags any MAC address that appears >= min_events times. Useful for catching
    VMware NIC resets, 802.1X re-auth loops, or flapping endpoints that Central
    does not surface as a port-flapping alert.

    Returns flagged clients sorted by event count descending, plus a summary.
    """
    events = get_mcp_client().get_events(serial_number, hours=hours, api_limit=1000)

    onboard_events = [
        e for e in events
        if e.get("eventName") == "Client Onboarding" and e.get("clientMacAddress")
    ]

    counts: dict[str, list[str]] = {}
    for e in onboard_events:
        mac = e["clientMacAddress"]
        ts = e.get("timeAt", "")
        counts.setdefault(mac, []).append(ts)

    flagged = [
        {
            "mac": mac,
            "event_count": len(timestamps),
            "first_seen": min(timestamps) if timestamps else None,
            "last_seen": max(timestamps) if timestamps else None,
            "source_name": next(
                (e.get("sourceName") for e in onboard_events if e.get("clientMacAddress") == mac),
                None,
            ),
        }
        for mac, timestamps in counts.items()
        if len(timestamps) >= min_events
    ]
    flagged.sort(key=lambda x: x["event_count"], reverse=True)

    return {
        "serial_number": serial_number,
        "hours_analyzed": hours,
        "min_events_threshold": min_events,
        "total_onboard_events": len(onboard_events),
        "flagged_clients": flagged,
        "flagged_count": len(flagged),
    }


@mcp.tool(annotations=READ_ONLY)
def detect_ssh_brute_force(
    serial_number: str,
    hours: int = 24,
    min_failures: int = 3,
) -> dict[str, Any]:
    """Detect SSH brute-force or misconfigured clients targeting a switch.

    Scans switch events for SSH login failures (eventId 5210) and SSH session
    denials (eventId 5214), groups by source IP, and flags any IP that hits
    >= min_failures within the time window.

    Returns flagged IPs sorted by failure count descending.
    """
    import re

    events = get_mcp_client().get_events(serial_number, hours=hours, api_limit=1000)

    ssh_events = [
        e for e in events
        if str(e.get("eventId", "")) in ("5210", "5214")
    ]

    _ip_re = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")

    ip_failures: dict[str, list[dict[str, Any]]] = {}
    for e in ssh_events:
        desc = e.get("description", "")
        match = _ip_re.search(desc)
        ip = match.group(1) if match else "unknown"
        ip_failures.setdefault(ip, []).append({
            "event_id": e.get("eventId"),
            "event_name": e.get("eventName"),
            "description": desc,
            "time": e.get("timeAt"),
        })

    flagged = [
        {
            "source_ip": ip,
            "failure_count": len(evts),
            "first_seen": min(e["time"] for e in evts if e["time"]) if evts else None,
            "last_seen": max(e["time"] for e in evts if e["time"]) if evts else None,
            "event_types": list({e["event_name"] for e in evts}),
        }
        for ip, evts in ip_failures.items()
        if len(evts) >= min_failures
    ]
    flagged.sort(key=lambda x: x["failure_count"], reverse=True)

    return {
        "serial_number": serial_number,
        "hours_analyzed": hours,
        "min_failures_threshold": min_failures,
        "total_ssh_failure_events": len(ssh_events),
        "flagged_sources": flagged,
        "flagged_count": len(flagged),
    }


@mcp.tool(annotations=READ_ONLY)
def get_site_health_summary(
    site_id: str | None = None,
    site_name: str | None = None,
) -> dict[str, Any]:
    """Return a single-view health summary for a site.

    Aggregates: device status counts, client count, active alert counts by
    severity, and recent notable switch/AP events (last 24h). Either site_id
    or site_name must be provided.
    """
    client = get_mcp_client()

    if not site_id and site_name:
        site = client.get_site_by_name(site_name)
        if not site:
            return {"error": f"Site not found: {site_name}"}
        site_id = site.get("scopeId") or site.get("siteId") or site.get("id")
        resolved_name = site.get("scopeName") or site.get("siteName") or site_name
    elif site_id:
        resolved_name = site_id
    else:
        return {"error": "Provide site_id or site_name"}

    devices = client.get_devices(filters={"siteId": site_id} if site_id else {}, limit=200)
    clients = client.get_clients(site_id=site_id, limit=200)
    alerts = client.get_alerts(site_id=site_id, limit=200)

    device_status: dict[str, int] = {}
    device_type_counts: dict[str, int] = {}
    for d in devices:
        status = (d.get("status") or "UNKNOWN").upper()
        device_status[status] = device_status.get(status, 0) + 1
        dtype = (d.get("deviceType") or "UNKNOWN").upper()
        device_type_counts[dtype] = device_type_counts.get(dtype, 0) + 1

    alert_severity: dict[str, int] = {}
    for a in alerts:
        sev = (a.get("severity") or "UNKNOWN").upper()
        alert_severity[sev] = alert_severity.get(sev, 0) + 1

    notable_event_names = {
        "INTERFACE", "Device Down", "Device Up", "Client Onboarding",
        "SSH User Login Failure", "SSH Failure", "PoE",
    }
    recent_events: list[dict[str, Any]] = []
    switches = [d for d in devices if "SWITCH" in (d.get("deviceType") or "").upper()]
    for sw in switches[:5]:
        serial = sw.get("serialNumber") or sw.get("id", "")
        if not serial:
            continue
        evts = client.get_events(serial, hours=24, api_limit=200)
        for e in evts:
            if e.get("eventName") in notable_event_names:
                recent_events.append({
                    "device": sw.get("deviceName") or serial,
                    "event": e.get("eventName"),
                    "description": e.get("description"),
                    "time": e.get("timeAt"),
                })
    recent_events.sort(key=lambda x: x.get("time") or "", reverse=True)

    return {
        "site": resolved_name,
        "site_id": site_id,
        "devices": {
            "total": len(devices),
            "by_status": device_status,
            "by_type": device_type_counts,
        },
        "clients": {
            "total": len(clients),
        },
        "alerts": {
            "total": len(alerts),
            "by_severity": alert_severity,
        },
        "recent_notable_events": recent_events[:20],
    }


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    from mcp_servers.shared import run_server
    run_server(mcp)
