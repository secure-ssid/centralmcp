"""MCP server — Aruba Central read-only monitoring tools (31 tools).

Covers: sites, devices, clients, alerts, events, scopes, inventory,
audit logs, device health/trends, switch ports/VLANs/PoE, AP radios/ports,
SLE metrics, WLANs, gateway clusters.
"""
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    READ_ONLY,
    bound_collection_response,
    clamp_limit,
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
# New Central API. No peer MCP (pycentral, KarthikSKumar98/central-mcp-server,
# nowireless4u/hpe-networking-mcp, gl-mcp) wraps SLE either. Bring it back
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
