"""MCP server — Aruba Central configuration and provisioning tools (32 tools).

Covers: VLANs, SSIDs, port profiles, firmware compliance, device management,
webhooks, device groups. Always use dry_run=True first for write operations.
"""
import os
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import get_client, get_mcp_client
from pipeline.config import build_account_contexts
from pipeline.ssid_underlay import (
    build_overlay_ssid as _build_overlay,
    build_underlay_ssid as _build,
    create_allow_all_role as _create_role,
    delete_underlay_ssid as _delete,
    get_underlay_ssid as _get,
    list_underlay_ssids as _list,
)
from pipeline.stages.s6_configure import (
    ARUBA_DEVICE_PROFILES,
    _ensure_device_profiles,
    _fetch_global_scope_id,
    _post_scope_map,
    _push_vlan_interface,
)

mcp = FastMCP("aruba-config")

_WEBHOOKS_BASE = "/network-services/v1/webhooks"
_DEVICE_GROUPS_BASE = "/network-config/v1/device-groups"


# ── VLANs ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_vlan(
    vlan_id: int,
    vlan_name: str | None = None,
    scope_id: str | None = None,
    persona: str = "ACCESS_SWITCH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an L2 VLAN and scope-map it (org-wide if scope_id omitted). Always dry_run first."""
    if dry_run:
        return {"vlan_id": vlan_id, "dry_run": True, "created": False}

    client = get_client()
    if scope_id is None:
        scope_id = _fetch_global_scope_id(client)

    name = vlan_name or str(vlan_id)
    body = {"vlan": vlan_id, "name": name, "enable": True}
    errors: list[str] = []

    try:
        client.post(f"/network-config/v1/layer2-vlan/{vlan_id}", data=body)
    except Exception as exc:
        resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "duplicate" in resp_text.lower():
            try:
                client.put(f"/network-config/v1/layer2-vlan/{vlan_id}", data=body)
            except Exception as exc2:
                errors.append(f"upsert: {exc2}")
        else:
            errors.append(f"create: {exc}")

    try:
        _post_scope_map(client, scope_id, persona, f"layer2-vlan/{vlan_id}")
    except Exception as exc:
        resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "already exists" not in resp_text.lower():
            errors.append(f"scope_map: {exc}")

    return {"vlan_id": vlan_id, "vlan_name": name, "scope_id": scope_id, "errors": errors}


@mcp.tool()
def create_vlan_interface(
    vlan_id: int,
    device_scope_id: str,
    ip_address: str | None = None,
    helper_address: str | None = None,
    dhcp: bool = False,
    persona: str = "ACCESS_SWITCH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an L3 SVI at device scope (global L2 VLAN shell is auto-confirmed). Always dry_run first.

    Args:
        device_scope_id: Device's numeric scope-id (use find_device).
        ip_address: CIDR notation e.g. "10.1.200.1/24". Omit for DHCP.
    """
    if dry_run:
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "dry_run": True}

    client = get_client()
    global_scope_id = _fetch_global_scope_id(client)
    vi = {"vlan": vlan_id, "ip_address": ip_address, "helper_address": helper_address, "dhcp": dhcp}

    try:
        _push_vlan_interface(client, vi, device_scope_id, global_scope_id, persona)
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "pushed": True, "errors": []}
    except Exception as exc:
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "pushed": False, "errors": [str(exc)]}


# ── Hostname & Device Profiles ─────────────────────────────────────────────────

@mcp.tool()
def set_hostname(
    device_scope_id: str,
    hostname: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set the hostname alias on a device. Always dry_run first.

    Args:
        device_scope_id: Device's numeric scope-id (use find_device).
    """
    if dry_run:
        return {"device_scope_id": device_scope_id, "hostname": hostname, "dry_run": True}

    client = get_client()
    try:
        client.post(
            "/network-config/v1/aliases/sys_host_name",
            params={"view-type": "LOCAL", "scope-id": device_scope_id},
            data={"name": "sys_host_name", "type": "ALIAS_HOSTNAME",
                  "default-value": {"hostname-value": {"hostname": hostname}}},
        )
        return {"device_scope_id": device_scope_id, "hostname": hostname, "set": True, "errors": []}
    except Exception as exc:
        return {"device_scope_id": device_scope_id, "hostname": hostname, "set": False, "errors": [str(exc)]}


@mcp.tool()
def push_aruba_device_profiles(dry_run: bool = False) -> dict[str, Any]:
    """Ensure the four standard Aruba LLDP device profiles exist at library level (idempotent)."""
    if dry_run:
        return {"profiles": [p["name"] for p in ARUBA_DEVICE_PROFILES], "dry_run": True}

    client = get_client()
    creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
    _, target_ctx = build_account_contexts(creds_path)
    target_ctx.central_client = client
    _ensure_device_profiles(client, target_ctx)
    return {"profiles": [p["name"] for p in ARUBA_DEVICE_PROFILES], "pushed": True}


# ── Firmware ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_firmware(serial_number: str) -> dict[str, Any]:
    """Fetch current firmware details (version, compliance status, upgrades available) for a device."""
    client = get_client()
    errors: list[str] = []
    try:
        result = client.get(
            "/network-services/v1alpha1/firmware-details",
            params={"serialNumber": serial_number},
        )
        return {"serial_number": serial_number, "items": result.get("items", []), "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"serial_number": serial_number, "items": [], "errors": errors}


@mcp.tool()
def get_firmware_compliance(
    scope_id: str,
    device_function: str,
) -> dict[str, Any]:
    """Read the current firmware compliance policy at a given scope."""
    client = get_client()
    errors: list[str] = []
    params = {"scope-id": scope_id, "object-type": "LOCAL", "device-function": device_function}
    try:
        response = client._request("GET", "/network-config/v1alpha1/firmware-compliance", params=params)
        if response.status_code not in (200, 201, 202):
            errors.append(f"HTTP {response.status_code}")
            return {"scope_id": scope_id, "device_function": device_function, "policy": None, "errors": errors}
        return {"scope_id": scope_id, "device_function": device_function, "policy": response.json(), "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"scope_id": scope_id, "device_function": device_function, "policy": None, "errors": errors}


@mcp.tool()
def set_firmware_compliance(
    scope_id: str,
    device_function: str,
    firmware_version: str,
    upgrade_mode: str = "REGULAR",
    reboot_schedule_mode: str = "IMMEDIATE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or update a firmware compliance policy (triggers upgrade). Always dry_run first.

    Args:
        scope_id: Use get_global_scope_id() for org-wide, or list_scopes() for a site/group.
        device_function: ACCESS_SWITCH, CAMPUS_AP, MOBILITY_GW, AGG_SWITCH, or CORE_SWITCH.
        firmware_version: Target version e.g. "10.16.1030".
        upgrade_mode: REGULAR (default) or LIVE.
    """
    client = get_client()
    errors: list[str] = []
    payload: dict[str, Any] = {
        "version-chart": {"version": firmware_version},
        "upgrade-mode": upgrade_mode,
        "enforcement-schedule": {
            "upgrade-schedule": {"upgrade-schedule-mode": "IMMEDIATE"},
            "reboot-schedule": {"reboot-schedule-mode": reboot_schedule_mode},
        },
    }
    params = {"scope-id": scope_id, "object-type": "LOCAL", "device-function": device_function}

    if dry_run:
        return {"dry_run": True, "action": "would POST or PATCH", "scope_id": scope_id,
                "device_function": device_function, "firmware_version": firmware_version,
                "payload": payload, "errors": []}

    action = "created"
    try:
        response = client._request("POST", "/network-config/v1alpha1/firmware-compliance", json=payload, params=params)
        if response.status_code == 412:
            action = "updated"
            response = client._request("PATCH", "/network-config/v1alpha1/firmware-compliance", json=payload, params=params)
        if response.status_code not in (200, 201, 202):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {"action": None, "scope_id": scope_id, "device_function": device_function,
                    "firmware_version": firmware_version, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"action": action, "scope_id": scope_id, "device_function": device_function,
                "firmware_version": firmware_version, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"action": None, "scope_id": scope_id, "device_function": device_function,
                "firmware_version": firmware_version, "response": None, "errors": errors}


@mcp.tool()
def list_firmware_upgrades(serial_number: str | None = None) -> dict[str, Any]:
    """List in-progress or recent firmware upgrade tasks."""
    client = get_client()
    errors: list[str] = []
    params: dict[str, Any] = {}
    if serial_number:
        params["serialNumber"] = serial_number
    try:
        response = client._request("GET", "/firmware/v1/upgrade", params=params or None)
        if response.status_code == 404:
            errors.append("GET /firmware/v1/upgrade returned 404 — not available on this instance.")
            return {"items": [], "errors": errors}
        response.raise_for_status()
        data = response.json() if response.text else {}
        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = [items] if items else []
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def trigger_device_upgrade(
    serial_number: str,
    firmware_version: str,
    device_function: str | None = None,
    reboot_schedule_mode: str = "IMMEDIATE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Trigger an immediate per-device firmware upgrade (bypasses compliance policy). Always dry_run first.

    Args:
        device_function: AUTO-detected if omitted; otherwise ACCESS_SWITCH, CAMPUS_AP, etc.
    """
    client = get_client()
    errors: list[str] = []

    if not device_function:
        device = get_mcp_client().get_device_by_serial(serial_number)
        if device:
            raw = device.get("deviceType", "")
            if "ACCESS_POINT" in raw or raw == "AP":
                device_function = "CAMPUS_AP"
            elif "SWITCH" in raw:
                device_function = "ACCESS_SWITCH"
            elif "GATEWAY" in raw:
                device_function = "MOBILITY_GW"

    if not device_function:
        errors.append(f"Could not determine device_function for {serial_number}.")
        return {"serial_number": serial_number, "firmware_version": firmware_version, "response": None, "errors": errors}

    payload: dict[str, Any] = {
        "firmware-version": firmware_version,
        "device-function": device_function,
        "reboot-schedule-mode": reboot_schedule_mode,
        "devices": [{"serial-number": serial_number}],
    }

    if dry_run:
        return {"dry_run": True, "serial_number": serial_number, "firmware_version": firmware_version,
                "device_function": device_function, "payload": payload, "errors": []}

    for endpoint in [
        "/network-config/v1alpha1/device-firmware-upgrade",
        "/network-config/v1/device-firmware-upgrade",
    ]:
        try:
            response = client._request("POST", endpoint, json=payload)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            try:
                resp_body = response.json()
            except Exception:
                resp_body = {}
            return {"serial_number": serial_number, "firmware_version": firmware_version,
                    "device_function": device_function, "endpoint_used": endpoint,
                    "response": resp_body, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "firmware_version": firmware_version, "response": None, "errors": errors}


# ── SSIDs ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_ssids() -> list[dict[str, Any]]:
    """Return all SSID objects from Aruba New Central."""
    return _list(get_client())


@mcp.tool()
def get_ssid(ssid_name: str) -> dict[str, Any] | None:
    """Fetch an existing SSID config by name. Returns None if not found."""
    return _get(get_client(), ssid_name)


@mcp.tool()
def get_scope_maps(resource_filter: str | None = None) -> list[dict[str, Any]]:
    """Return all scope-map entries, optionally filtered by resource name."""
    client = get_client()
    maps = client.get("/network-config/v1/scope-maps")
    if resource_filter and isinstance(maps, list):
        return [m for m in maps if resource_filter.lower() in str(m.get("resource", "")).lower()]
    return maps if isinstance(maps, list) else []


@mcp.tool()
def list_gw_clusters() -> list[dict[str, Any]]:
    """List available gateway clusters for use with overlay/tunneled SSIDs."""
    return get_mcp_client().get_gw_clusters()


@mcp.tool()
def build_underlay_ssid(
    ssid_name: str,
    scope_id: str,
    persona: str = "CAMPUS_AP",
    opmode: str = "WPA3_SAE_AES",
    passphrase: str | None = None,
    vlan_id: int | None = None,
    vlan_ids: list[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a bridge-mode (underlay) SSID and scope-map it. Always dry_run first.

    Args:
        scope_id: Use get_global_scope_id() for org-wide, or list_scopes() for site/group.
        persona: CAMPUS_AP (default), MOBILITY_GW, ACCESS_SWITCH, etc.
        opmode: WPA3_SAE_AES, WPA3_WPA2_AES, WPA2_AES, or OPEN_NETWORK.
        passphrase: Required for WPA2/WPA3-PSK modes.
        vlan_id / vlan_ids: Single VLAN or list of VLAN IDs.
    """
    client = get_client()
    return _build(
        client=client,
        ssid_name=ssid_name,
        scope_id=scope_id,
        persona=persona,
        opmode=opmode,
        passphrase=passphrase,
        vlan_id=vlan_id,
        vlan_ids=vlan_ids,
        dry_run=dry_run,
    )


@mcp.tool()
def build_overlay_ssid(
    ssid_name: str,
    scope_id: str,
    cluster_name: str,
    persona: str = "CAMPUS_AP",
    opmode: str = "WPA3_SAE_AES",
    passphrase: str | None = None,
    vlan_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a tunneled (overlay/GRE) SSID via a gateway cluster. Always dry_run first.

    Args:
        cluster_name: Gateway cluster name (use list_gw_clusters).
        scope_id: Use get_global_scope_id() or list_scopes().
    """
    client = get_client()
    return _build_overlay(
        client=client,
        ssid_name=ssid_name,
        scope_id=scope_id,
        cluster_name=cluster_name,
        persona=persona,
        opmode=opmode,
        passphrase=passphrase,
        vlan_id=vlan_id,
        dry_run=dry_run,
    )


@mcp.tool()
def create_allow_all_role(
    role_name: str,
    scope_id: str,
    persona: str = "CAMPUS_AP",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a permit-all wireless role and scope-map it. Always dry_run first."""
    client = get_client()
    return _create_role(
        client=client,
        role_name=role_name,
        scope_id=scope_id,
        persona=persona,
        dry_run=dry_run,
    )


@mcp.tool()
def delete_underlay_ssid(
    ssid_name: str,
    scope_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an underlay SSID and its scope-map. Always dry_run first."""
    client = get_client()
    return _delete(client=client, ssid_name=ssid_name, scope_id=scope_id, dry_run=dry_run)


@mcp.tool()
def update_ssid(
    ssid_name: str,
    updates: dict[str, Any],
    scope_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """PATCH an existing SSID — only provided fields are changed. Always dry_run first.

    Args:
        updates: Fields to change, e.g. {"enable": True, "wpa-passphrase": "newpass"}.
        scope_id: Optional — for a scope-specific LOCAL override.
    """
    client = get_client()
    errors: list[str] = []
    url_name = quote(ssid_name, safe="")
    endpoint = f"/network-config/v1/wlan-ssids/{url_name}"
    params: dict[str, Any] = {}
    if scope_id:
        params["scope-id"] = scope_id
        params["view-type"] = "LOCAL"

    if dry_run:
        return {"dry_run": True, "ssid_name": ssid_name, "scope_id": scope_id, "updates": updates, "response": None, "errors": []}

    try:
        response = client._request("PATCH", endpoint, json=updates, params=params or None)
        if response.status_code not in (200, 201, 202, 204):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {"ssid_name": ssid_name, "scope_id": scope_id, "updates": updates, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"ssid_name": ssid_name, "scope_id": scope_id, "updates": updates, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"ssid_name": ssid_name, "scope_id": scope_id, "updates": updates, "response": None, "errors": errors}


# ── Switch Port Profiles & Interface Config ────────────────────────────────────

@mcp.tool()
def create_port_profile(
    profile_name: str,
    body: dict[str, Any],
    description: str = "",
    scope_ids: list[str] | None = None,
    persona: str = "ACCESS_SWITCH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create (or update) a switch port profile and scope-map it. Always dry_run first.

    Two-step: POST (shell) then PUT (full config body). Pass the nested config dict as body.
    """
    if dry_run:
        return {"dry_run": True, "profile_name": profile_name, "description": description,
                "body": body, "scope_ids": scope_ids or [], "errors": []}

    client = get_client()
    errors: list[str] = []
    encoded_name = quote(profile_name, safe="")

    try:
        client.post(f"/network-config/v1/sw-port-profiles/{encoded_name}", data={"description": description})
    except Exception as exc:
        resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "duplicate" not in resp_text.lower() and "already exists" not in resp_text.lower():
            errors.append(f"POST (shell): {exc}")

    try:
        client.put(f"/network-config/v1/sw-port-profiles/{encoded_name}", data=body)
    except Exception as exc:
        errors.append(f"PUT (body): {exc}")

    for sid in (scope_ids or []):
        try:
            _post_scope_map(client, sid, persona, f"sw-port-profiles/{profile_name}")
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
            if "already exists" not in resp_text.lower():
                errors.append(f"scope_map(scope={sid}): {exc}")

    return {"profile_name": profile_name, "body": body, "scope_ids": scope_ids or [], "errors": errors}


@mcp.tool()
def update_port_config(
    serial_number: str,
    interface_name: str,
    updates: dict[str, Any],
    device_scope_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """PATCH ethernet interface config on a CX switch at device scope. Always dry_run first.

    Args:
        interface_name: e.g. "1/1/6".
        device_scope_id: Device's config-layer scope-id (use find_device).
        updates: Fields to PATCH, e.g. {"port-profile": "ap-uplink", "admin-state": "UP"}.
    """
    if dry_run:
        return {"dry_run": True, "serial_number": serial_number, "interface_name": interface_name,
                "updates": updates, "device_scope_id": device_scope_id, "response": None, "errors": []}

    client = get_client()
    errors: list[str] = []
    encoded_iface = quote(interface_name, safe="")
    endpoint = f"/network-config/v1/ethernet-interfaces/{encoded_iface}"
    params = {"viewtype": "LOCAL", "scope-id": device_scope_id}

    try:
        response = client._request("PATCH", endpoint, json=updates, params=params)
        if response.status_code not in (200, 201, 202, 204):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {"serial_number": serial_number, "interface_name": interface_name,
                    "updates": updates, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"serial_number": serial_number, "interface_name": interface_name,
                "updates": updates, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"serial_number": serial_number, "interface_name": interface_name,
                "updates": updates, "response": None, "errors": errors}


# ── Device Management ─────────────────────────────────────────────────────────

@mcp.tool()
def assign_device_to_site(
    serial_number: str,
    site_id: str,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Assign or move a device to a site.

    Args:
        site_id: Target site ID (use list_sites).
        device_type: Optional hint — "SWITCH", "AP", or "GATEWAY".
    """
    client = get_client()
    errors: list[str] = []

    candidates = [
        ("POST", f"/network-monitoring/v1/sites/{site_id}/devices", {"serials": [serial_number]}),
        ("POST", "/monitoring/v1/site/assign",
         {"site_id": int(site_id), "device_id": [serial_number],
          **({"device_type": device_type} if device_type else {})}),
    ]

    for method, endpoint, payload in candidates:
        try:
            response = client._request(method, endpoint, json=payload)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            try:
                resp_body = response.json()
            except Exception:
                resp_body = {}
            return {"serial_number": serial_number, "site_id": site_id, "endpoint_used": endpoint,
                    "response": resp_body, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "site_id": site_id, "response": None, "errors": errors}


@mcp.tool()
def update_device_settings(
    serial_number: str,
    settings: dict[str, Any],
    device_scope_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update device metadata or settings (name, location, notes, banner). Always dry_run first.

    Args:
        device_scope_id: Required for switch-system config path.
    """
    if dry_run:
        return {"dry_run": True, "serial_number": serial_number, "settings": settings,
                "device_scope_id": device_scope_id, "response": None, "errors": []}

    client = get_client()
    errors: list[str] = []
    candidates: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = [
        ("PATCH", f"/network-monitoring/v1/devices/{serial_number}", settings, None),
    ]
    if device_scope_id:
        candidates.append((
            "PATCH", f"/network-config/v1/switch-system/{serial_number}", settings,
            {"viewtype": "LOCAL", "scope-id": device_scope_id},
        ))

    for method, endpoint, payload, params in candidates:
        try:
            response = client._request(method, endpoint, json=payload, params=params)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202, 204):
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            try:
                resp_body = response.json()
            except Exception:
                resp_body = {}
            return {"serial_number": serial_number, "settings": settings, "endpoint_used": endpoint,
                    "response": resp_body, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "settings": settings, "endpoint_used": None, "response": None, "errors": errors}


# ── Webhooks ──────────────────────────────────────────────────────────────────

@mcp.tool()
def list_webhooks() -> dict[str, Any]:
    """List all configured webhooks."""
    return get_client().get(_WEBHOOKS_BASE)


@mcp.tool()
def create_webhook(
    name: str,
    endpoint_url: str,
    auth_mechanism: str = "API_KEY",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Create a new webhook.

    Args:
        auth_mechanism: "API_KEY" (default) or "HMAC".
    """
    client = get_client()
    payload: dict[str, Any] = {"input": {"name": name, "endpoint": endpoint_url, "authMechanism": auth_mechanism}}
    if api_key:
        payload["input"]["apiKey"] = api_key
    resp = client._request("POST", _WEBHOOKS_BASE, json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def get_webhook(webhook_id: str) -> dict[str, Any]:
    """Fetch details for a single webhook by ID."""
    return get_client().get(f"{_WEBHOOKS_BASE}/{webhook_id}")


@mcp.tool()
def update_webhook(
    webhook_id: str,
    name: str | None = None,
    endpoint_url: str | None = None,
    auth_mechanism: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """PATCH an existing webhook — only provided fields are changed."""
    client = get_client()
    patch: dict[str, Any] = {}
    if name is not None:
        patch["name"] = name
    if endpoint_url is not None:
        patch["endpoint"] = endpoint_url
    if auth_mechanism is not None:
        patch["authMechanism"] = auth_mechanism
    if api_key is not None:
        patch["apiKey"] = api_key
    resp = client._request("PATCH", f"{_WEBHOOKS_BASE}/{webhook_id}", json={"input": patch})
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_webhook(webhook_id: str) -> dict[str, Any]:
    """Delete a webhook by ID."""
    client = get_client()
    resp = client._request("DELETE", f"{_WEBHOOKS_BASE}/{webhook_id}")
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "response": body}


@mcp.tool()
def rotate_webhook_key(webhook_id: str) -> dict[str, Any]:
    """Rotate the HMAC key for a webhook."""
    client = get_client()
    resp = client._request("POST", f"{_WEBHOOKS_BASE}/{webhook_id}/rotate-hmac-key")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── Device Groups ─────────────────────────────────────────────────────────────

@mcp.tool()
def list_device_groups(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List all device groups (scopeId, scopeName, description)."""
    return get_client().get(f"{_DEVICE_GROUPS_BASE}?limit={limit}&offset={offset}")


@mcp.tool()
def create_device_group(
    name: str,
    description: str = "",
    devices: list[str] | None = None,
) -> dict[str, Any]:
    """Create a device group, optionally pre-populated with device serial numbers."""
    client = get_client()
    if devices:
        payload = {"scopeName": name, "description": description, "devices": devices}
        resp = client._request("POST", "/network-config/v1/device-groups-create-and-add-devices", json=payload)
    else:
        payload = {"scopeName": name, "description": description}
        resp = client._request("POST", _DEVICE_GROUPS_BASE, json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_device_groups(scope_ids: list[str]) -> dict[str, Any]:
    """Bulk-delete device groups by scope ID list."""
    client = get_client()
    payload = {"items": [{"id": sid} for sid in scope_ids]}
    resp = client._request("DELETE", f"{_DEVICE_GROUPS_BASE}/bulk", json=payload)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "response": body}


@mcp.tool()
def add_devices_to_group(scope_id: str, serial_numbers: list[str]) -> dict[str, Any]:
    """Add devices to an existing device group by scope ID."""
    client = get_client()
    payload = {"desScopeId": scope_id, "devices": serial_numbers}
    resp = client._request("POST", "/network-config/v1/device-groups-add-devices", json=payload)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "response": body}


@mcp.tool()
def remove_devices_from_group(serial_numbers: list[str]) -> dict[str, Any]:
    """Remove devices from their current device group."""
    client = get_client()
    resp = client._request("POST", "/network-config/v1/device-groups-remove-devices", json={"devices": serial_numbers})
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return {"status_code": resp.status_code, "response": body}


if __name__ == "__main__":
    mcp.run()
