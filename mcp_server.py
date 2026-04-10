"""MCP server — Aruba New Central tools.

Exposes the following tools to Claude (or any MCP client):

  READ
  ----
  list_sites               List all sites with IDs
  get_site                 Find a site by name
  list_devices             List devices (optionally filtered)
  find_device              Find a device by serial number
  list_clients             List connected clients (optionally by site or device)
  find_client              Find a client by MAC or IP address
  get_client_details       Fetch detailed info (incl. usage/bandwidth) for a single client by MAC
  list_alerts              List active alerts (optionally by site or severity)
  list_events              List events for a device
  get_events_count         Count events for a device
  list_scopes              List all scopes (org, sites, device groups)
  get_global_scope_id      Discover the org-level global scope-id
  list_inventory           List claimed/unprovisioned devices in inventory
  list_audit_logs          List New Central audit log entries (last 24h by default)
  get_audit_log            Fetch a single audit log entry by ID

  WRITE
  -----
  build_underlay_ssid      Create + scope-map an underlay SSID (bridge mode / non-tunneled)
  build_overlay_ssid       Create + scope-map an overlay SSID (tunneled / GRE via gateway)
  list_gw_clusters         List available gateway clusters for tunneled overlay SSIDs
  create_allow_all_role    Create a permit-all wireless role + scope-map it
  delete_underlay_ssid     Delete an underlay SSID
  get_ssid                 Fetch an existing SSID config
  list_ssids               List all SSID objects
  get_scope_maps           List scope-map entries, optionally filtered by resource
  create_vlan              Create an L2 VLAN and scope-map it globally
  create_vlan_interface    Create an L3 VLAN interface (SVI) at device scope
  set_hostname             Set the hostname alias on a device
  push_aruba_device_profiles  Ensure Aruba LLDP device profiles exist at library level
  get_firmware             Fetch current firmware details for a device
  get_firmware_compliance  Read the firmware compliance policy at a scope
  set_firmware_compliance  Create or update a firmware compliance policy (triggers upgrade)
  list_firmware_upgrades   List in-progress or recent firmware upgrade tasks
  cx_ping                  Ping a destination from a CX switch (async, polls to completion)
  cx_traceroute            Traceroute to a destination from a CX switch (async, polls to completion)
  cx_show                  Run one or more 'show' commands on a CX switch (async, polls to completion)
  update_ssid              Update an existing SSID (general field PATCH)
  trigger_device_upgrade   Trigger an immediate per-device firmware upgrade (bypasses policy)
  reboot_device            Reboot an AP, switch, or gateway
  assign_device_to_site    Assign or move a device to a different site
  acknowledge_alert        Acknowledge, clear, or resolve an active alert
  disconnect_client        Force-disconnect a wireless client by MAC address
  update_device_settings   Update general device-level metadata/settings
  get_device_trends        Fetch time-series utilization trends for an AP or switch (cpu, memory, throughput)
  get_device_health        Fetch config-health or monitoring health state for a device
  get_wireless_metrics     Fetch AP-specific wireless metrics (RF, clients, utilization)
  list_switch_ports        List interfaces on a switch with link state, speed, and VLAN info
  get_switch_details       Fetch full monitoring details for a switch (uptime, CPU, memory, VLANs)
  get_switch_vlans         List VLANs active on a switch with status and membership
  get_switch_interface_poe Fetch PoE state and power draw for all switch ports
  get_switch_interface_trends  Fetch throughput trends for switch interfaces over a time window
  get_ap_radios            List radios on an AP (band, channel, power, utilization)
  get_ap_ports             List wired ports on an AP (link state, speed, VLAN)
  get_sle_metrics          Fetch SLE (Service Level Experience) scores by site or device
  create_port_profile      Create a switch port profile and scope-map it
  update_port_config       Update ethernet interface config on a CX switch (device scope)
  poe_bounce               Bounce PoE on switch/gateway ports (CX, AOS-S, Gateway)
  port_bounce              Bounce link on switch/gateway ports (CX, AOS-S, Gateway)
  cable_test               Run cable/TDR test on switch ports (CX, AOS-S)

  GREENLAKE PLATFORM (GLP)
  ------------------------
  list_glp_devices         List GLP workspace device inventory (ownership, warranty, subscription state)
  get_glp_device           Fetch a single GLP device by serial number
  list_glp_subscriptions   List GLP subscription (license) keys and their expiry/assignment
  get_glp_subscription     Fetch a single GLP subscription by ID
  list_glp_users           List users with access to the GLP workspace
  list_glp_audit_logs      List GLP audit log entries (who did what, when)
  glp_assign_subscription  Assign a license/subscription to a device
  glp_add_device           Add a device to the GLP workspace (async, waits for completion)
  glp_archive_device       Archive a device in GLP (removes from Central, keeps in GLP)

Credentials are loaded from config/credentials.yaml (or env vars —
see pipeline/config.py).  Set CREDS_PATH env var to override the path.

--- NAMING CONVENTIONS ---

Tool names follow verb_noun with no prefix:
  list_*   — return multiple items
  get_*    — return one item or a specific value
  find_*   — search/lookup (returns None if not found)
  create_* — create a resource (idempotent where possible)
  set_*    — update a single attribute
  push_*   — bulk create/upsert
  delete_* — remove a resource

All new tools MUST follow this pattern. Do not add prefixes like
"central_" or "aruba_" — the server name already provides that context.

--- HOW TO HANDLE USER REQUESTS ---

Users speak in Central GUI terms, not API terms. Translate as follows:

  "scope" in the API = where in the hierarchy the config is applied
    - "everywhere" / "all APs" / "org-wide" → global scope (call get_global_scope_id())
    - a site name (e.g. "Home Lab", "Dallas Office") → a site scope
    - a group name (e.g. "New Central APs", "Branch APs") → a device group scope
    - For site/group names: call list_scopes() and match by scope_name

  "persona" in the API = device type in the Central UI
    - "Access Points" / "APs" / "wireless" → CAMPUS_AP
    - "Gateways" / "GW"                   → MOBILITY_GW
    - "Access Switch" / "access layer"     → ACCESS_SWITCH
    - "Aggregation Switch" / "agg switch"  → AGG_SWITCH
    - "Core Switch"                        → CORE_SWITCH
    Default to CAMPUS_AP for any SSID/wireless request unless told otherwise.

Before calling build_underlay_ssid or create_allow_all_role, ALWAYS confirm
these four things with the user (in plain language) if not already provided:

  1. WHERE: "Where should this apply — everywhere (org-wide), or a specific
     site or group? If a site or group, what's the name?"

  2. DEVICE TYPE: "Which devices should get this — Access Points, Gateways,
     or a switch type?" (Default: Access Points, unless told otherwise.)

  3. SECURITY (build_underlay_ssid only):
     "What security should this SSID use — open, WPA3 with support for older
      devices, WPA3-only, or WPA2 with a pre-shared key?"
     If WPA3 or WPA2-PSK: "What's the passphrase?"

  4. VLAN (build_underlay_ssid only): "Which VLAN ID(s) should this SSID use?"

  For create_allow_all_role:
     - Role name defaults to the SSID name. Confirm or ask.
     - Reuse the same WHERE and DEVICE TYPE answers from the SSID step.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from pipeline.clients.central_client import CentralClient
from pipeline.clients.glp_client import GLPClient
from pipeline.clients.mcp_client import MCPClient
from pipeline.clients.token_manager import TokenManager
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mcp = FastMCP("aruba-central")

# ---------------------------------------------------------------------------
# Shared clients (lazy-initialised once per process)
# ---------------------------------------------------------------------------

_central_client: CentralClient | None = None
_mcp_client: MCPClient | None = None


def _get_client() -> CentralClient:
    global _central_client
    if _central_client is None:
        creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
        _, target_ctx = build_account_contexts(creds_path)
        tm = TokenManager(
            client_id=target_ctx.client_id,
            client_secret=target_ctx.client_secret,
            cache_key="target",
        )
        _central_client = CentralClient(base_url=target_ctx.base_url, token_manager=tm)
    return _central_client


def _get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient(_get_client())
    return _mcp_client


_glp_client: GLPClient | None = None


def _get_glp_client() -> GLPClient:
    global _glp_client
    if _glp_client is None:
        creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
        _, target_ctx = build_account_contexts(creds_path)
        tm = TokenManager(
            client_id=target_ctx.client_id,
            client_secret=target_ctx.client_secret,
            cache_key="glp",
        )
        _glp_client = GLPClient(
            token_manager=tm,
            workspace_id=target_ctx.glp_workspace_id,
        )
    return _glp_client


# ---------------------------------------------------------------------------
# READ — Sites
# ---------------------------------------------------------------------------


@mcp.tool()
def list_sites() -> list[dict[str, Any]]:
    """Return all sites in this Central account with their IDs and names.

    Each entry has: siteId (or id), siteName (or name), and any location fields.
    Use this to find a site_id for filtering alerts, clients, or events by location.
    """
    return _get_mcp_client().get_sites()


@mcp.tool()
def get_site(name: str) -> dict[str, Any] | None:
    """Find a site by name (case-insensitive). Returns the site record or None.

    Use this to resolve a user's plain-language site name to a site ID before
    filtering other queries by site.
    """
    return _get_mcp_client().get_site_by_name(name)


# ---------------------------------------------------------------------------
# READ — Devices
# ---------------------------------------------------------------------------


@mcp.tool()
def list_devices(
    device_type: str | None = None,
    site_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return device inventory records from New Central.

    Args:
        device_type: Optional filter — e.g. "SWITCH", "AP", "GATEWAY".
        site_id:     Optional site ID to filter by location.
    """
    filters: dict[str, Any] = {}
    if device_type:
        filters["deviceType"] = device_type
    if site_id:
        filters["siteId"] = site_id
    return _get_mcp_client().get_devices(filters or None)


@mcp.tool()
def find_device(serial_number: str) -> dict[str, Any] | None:
    """Find a single device by serial number. Returns the device record or None."""
    return _get_mcp_client().get_device_by_serial(serial_number)


# ---------------------------------------------------------------------------
# READ/WRITE — Firmware
# ---------------------------------------------------------------------------


@mcp.tool()
def get_firmware(serial_number: str) -> dict[str, Any]:
    """Fetch current firmware details for a device by serial number.

    Returns the items array from the firmware-details API, which includes
    the current firmware version, compliance status, and available upgrade versions.

    Args:
        serial_number: Device serial number (e.g. "CN26KNN2YQ").

    Returns:
        Dict with key "items" (list of firmware detail records) and "errors".

    Note:
        The firmwareVersion field includes a platform prefix, e.g. "PL.10.16.1006".
        To check if a device is on AOS 10, test: "10." in firmwareVersion.
    """
    client = _get_client()
    errors: list[str] = []
    try:
        result = client.get(
            "/network-services/v1alpha1/firmware-details",
            params={"serialNumber": serial_number},
        )
        items = result.get("items", [])
        return {"serial_number": serial_number, "items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"serial_number": serial_number, "items": [], "errors": errors}


@mcp.tool()
def get_firmware_compliance(
    scope_id: str | None = None,
    device_function: str | None = None,
    object_type: str | None = None,
) -> dict[str, Any]:
    """Read the firmware compliance policy at a given scope.

    Uses GET /network-config/v1alpha1/firmware-compliance.

    Args:
        scope_id:        Scope to query (site, group, or global scope-id).
                         If omitted, returns SHARED/library policies.
        device_function: Filter by device type — e.g. ACCESS_SWITCH, CAMPUS_AP,
                         MOBILITY_GW, AGG_SWITCH, CORE_SWITCH.
        object_type:     LOCAL or SHARED (default: both).

    Returns:
        Dict with key "items" (policy records) and "errors".
    """
    client = _get_client()
    errors: list[str] = []
    params: dict[str, Any] = {}
    if scope_id:
        params["scope-id"] = scope_id
        params.setdefault("object-type", "LOCAL")
    if device_function:
        params["device-function"] = device_function
    if object_type:
        params["object-type"] = object_type

    try:
        result = client.get("/network-config/v1alpha1/firmware-compliance", params=params or None)
        items = result.get("items", result)
        if not isinstance(items, list):
            items = [items] if items else []
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def set_firmware_compliance(
    scope_id: str,
    device_function: str,
    firmware_version: str,
    upgrade_mode: str = "REGULAR",
    reboot_schedule_mode: str = "IMMEDIATE",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or update a firmware compliance policy to trigger an upgrade.

    Uses POST /network-config/v1alpha1/firmware-compliance (creates if absent,
    then PATCH to update if a policy already exists at this scope).

    This is the correct way to upgrade switches and APs on this instance —
    the /firmware/v1/upgrade endpoint returns 404 here.

    Args:
        scope_id:            Scope where the policy applies — use get_global_scope_id()
                             for org-wide, or list_scopes() to find a site/group scope-id.
        device_function:     Target device type — ACCESS_SWITCH, CAMPUS_AP,
                             MOBILITY_GW, AGG_SWITCH, or CORE_SWITCH.
        firmware_version:    Target firmware version (e.g. "10.16.1030").
        upgrade_mode:        REGULAR (default) or LIVE (hitless/live upgrade).
        reboot_schedule_mode: IMMEDIATE (default), SINCE, or NEVER.
        dry_run:             Preview the payload without submitting.

    Returns:
        Dict with keys: action ("created" or "updated"), scope_id, device_function,
        firmware_version, response, errors.
    """
    client = _get_client()
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
        return {
            "dry_run": True,
            "action": "would POST or PATCH",
            "scope_id": scope_id,
            "device_function": device_function,
            "firmware_version": firmware_version,
            "payload": payload,
            "errors": [],
        }

    # Try POST first; if 412 (already exists), fall back to PATCH
    action = "created"
    try:
        response = client._request(
            "POST", "/network-config/v1alpha1/firmware-compliance", json=payload, params=params
        )
        if response.status_code == 412:
            # Policy already exists — update it
            action = "updated"
            response = client._request(
                "PATCH", "/network-config/v1alpha1/firmware-compliance", json=payload, params=params
            )
        if response.status_code not in (200, 201, 202):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {
                "action": None,
                "scope_id": scope_id,
                "device_function": device_function,
                "firmware_version": firmware_version,
                "response": None,
                "errors": errors,
            }
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {
            "action": action,
            "scope_id": scope_id,
            "device_function": device_function,
            "firmware_version": firmware_version,
            "response": resp_body,
            "errors": errors,
        }
    except Exception as exc:
        errors.append(str(exc))
        return {
            "action": None,
            "scope_id": scope_id,
            "device_function": device_function,
            "firmware_version": firmware_version,
            "response": None,
            "errors": errors,
        }


@mcp.tool()
def list_firmware_upgrades(
    serial_number: str | None = None,
) -> dict[str, Any]:
    """List in-progress or recent firmware upgrade tasks.

    Uses GET /firmware/v1/upgrade to fetch the upgrade job status.

    Args:
        serial_number: Optional — filter results to a specific device serial.

    Returns:
        Dict with key "items" (list of upgrade task records) and "errors".
    """
    client = _get_client()
    errors: list[str] = []
    params: dict[str, Any] = {}
    if serial_number:
        params["serialNumber"] = serial_number

    try:
        response = client._request("GET", "/firmware/v1/upgrade", params=params or None)
        if response.status_code == 404:
            errors.append(
                "GET /firmware/v1/upgrade returned 404 — endpoint not available on this instance."
            )
            return {"items": [], "errors": errors}
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            data = {}
        items = data.get("items", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = [items] if items else []
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


# ---------------------------------------------------------------------------
# READ — Clients
# ---------------------------------------------------------------------------


@mcp.tool()
def list_clients(
    site_id: str | None = None,
    serial_number: str | None = None,
) -> list[dict[str, Any]]:
    """Return connected clients, optionally filtered by site or device serial.

    Args:
        site_id:       Only return clients at this site.
        serial_number: Only return clients connected to this device.
    """
    return _get_mcp_client().get_clients(site_id=site_id, serial_number=serial_number)


@mcp.tool()
def find_client(mac_or_ip: str) -> dict[str, Any] | None:
    """Find a single connected client by MAC address or IP address. Returns None if not found."""
    return _get_mcp_client().find_client(mac_or_ip)


@mcp.tool()
def get_client_details(mac_address: str) -> dict[str, Any]:
    """Fetch detailed info for a single client by MAC address.

    Uses GET /network-monitoring/v1/clients/{mac-address} (classic v1 API).
    Returns richer detail than list_clients — including usage/bandwidth stats
    and historical connection info if available.

    Args:
        mac_address: Client MAC address (e.g. "80:4a:f2:4c:0f:e8").

    Returns:
        Dict with "client" (detail record) and "errors".
    """
    client = _get_client()
    errors: list[str] = []
    mac = mac_address.replace("-", ":").lower()
    try:
        response = client._request("GET", f"/network-monitoring/v1/clients/{mac}")
        if response.status_code == 404:
            errors.append(f"Client {mac} not found.")
            return {"client": None, "errors": errors}
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            data = {}
        return {"client": data, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"client": None, "errors": errors}


# ---------------------------------------------------------------------------
# READ — Alerts
# ---------------------------------------------------------------------------


@mcp.tool()
def list_alerts(
    site_id: str | None = None,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    """Return active alerts from New Central.

    Args:
        site_id:  Only return alerts for this site.
        severity: Filter by severity — e.g. "CRITICAL", "MAJOR", "MINOR", "WARNING".
    """
    return _get_mcp_client().get_alerts(site_id=site_id, severity=severity)


# ---------------------------------------------------------------------------
# READ — Events
# ---------------------------------------------------------------------------


@mcp.tool()
def list_events(serial_number: str, hours: int = 24) -> list[dict[str, Any]]:
    """Return events for a device within the last N hours (default 24).

    Args:
        serial_number: The device serial number.
        hours:         Look-back window — 24 or less uses last_24h; more uses last_7d.
    """
    return _get_mcp_client().get_events(serial_number=serial_number, hours=hours)


@mcp.tool()
def get_events_count(serial_number: str, hours: int = 24) -> dict[str, Any]:
    """Return the count of events for a device within the last N hours.

    Args:
        serial_number: The device serial number.
        hours:         Look-back window — 24 or less uses last_24h; more uses last_7d.
    """
    events = _get_mcp_client().get_events(serial_number=serial_number, hours=hours)
    return {"serial_number": serial_number, "hours": hours, "count": len(events)}


# ---------------------------------------------------------------------------
# READ — Scopes
# ---------------------------------------------------------------------------


@mcp.tool()
def list_scopes() -> list[dict[str, Any]]:
    """Return all scopes (locations/groups) in this Central account.

    Each entry has: scope_id, scope_name, scope_type.

    scope_type will be one of: org (the whole organisation), site (a physical
    location like "Dallas Office"), or group (a device group like "Branch APs").

    Use this to resolve a user's plain-language answer ("apply it to the Home Lab
    site" or "put it in the Branch APs group") to a numeric scope_id. Present the
    results as a simple list of names so the user can confirm which one to use.
    """
    client = _get_client()
    try:
        # Build a name lookup from sites and device-groups (both use scopeId + scopeName)
        name_map: dict[str, tuple[str, str]] = {}  # scope_id -> (name, type)

        # Org-level global scope
        global_id = str(_fetch_global_scope_id(client))
        name_map[global_id] = ("Global (Org-wide)", "org")

        try:
            sites_result = client.get("/network-config/v1/sites")
            for site in sites_result.get("items", []):
                sid = str(site.get("scopeId") or site.get("id", ""))
                sname = site.get("scopeName") or site.get("siteName") or sid
                if sid:
                    name_map[sid] = (sname, "site")
        except Exception:
            pass

        try:
            groups_result = client.get("/network-config/v1/device-groups")
            for grp in groups_result.get("items", []):
                sid = str(grp.get("scopeId") or grp.get("id", ""))
                sname = grp.get("scopeName") or sid
                if sid:
                    name_map[sid] = (sname, "group")
        except Exception:
            pass

        # Collect all unique scope-ids from scope-maps, then label them
        result = client.get("/network-config/v1/scope-maps")
        seen: dict[str, dict] = {}
        for entry in result.get("scope-map", []):
            sid = str(entry.get("scope-id", ""))
            if sid and sid not in seen:
                sname, stype = name_map.get(sid, (sid, ""))
                seen[sid] = {"scope_id": sid, "scope_name": sname, "scope_type": stype}

        # Ensure org/site/group entries are included even if no scope-map exists yet
        for sid, (sname, stype) in name_map.items():
            if sid not in seen:
                seen[sid] = {"scope_id": sid, "scope_name": sname, "scope_type": stype}

        return list(seen.values())
    except Exception as exc:
        return [{"error": str(exc)}]


@mcp.tool()
def get_global_scope_id() -> dict[str, Any]:
    """Discover and return the org-level global scope-id for this Central account.

    Call this automatically when the user says "org-wide", "global", or doesn't
    specify a particular site or device group.
    """
    scope_id = _fetch_global_scope_id(_get_client())
    return {"scope_id": scope_id}


# ---------------------------------------------------------------------------
# WRITE — VLANs
# ---------------------------------------------------------------------------


@mcp.tool()
def create_vlan(
    vlan_id: int,
    vlan_name: str | None = None,
    scope_id: str | None = None,
    persona: str = "ACCESS_SWITCH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an L2 VLAN in New Central and scope-map it.

    If scope_id is omitted, the VLAN is mapped at the global (org-wide) scope.

    Args:
        vlan_id:   VLAN ID (1–4094).
        vlan_name: Human-readable name (defaults to the VLAN ID as a string).
        scope_id:  Scope to map the VLAN to. Omit for org-wide.
        persona:   Device type for the scope-map (default ACCESS_SWITCH).
        dry_run:   Preview without writing to Central.
    """
    if dry_run:
        return {"vlan_id": vlan_id, "dry_run": True, "created": False}

    client = _get_client()
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
    """Create an L3 VLAN interface (SVI) at device scope in New Central.

    The global L2 VLAN shell is also created/confirmed automatically.

    Args:
        vlan_id:          VLAN ID for the SVI.
        device_scope_id:  The device's numeric scope-id (use find_device or list_scopes).
        ip_address:       IP address in CIDR notation, e.g. "10.1.200.1/24". Omit for DHCP.
        helper_address:   DHCP relay/helper IP (optional).
        dhcp:             Set True if the interface should use DHCP instead of a static IP.
        persona:          Device type for scope-maps (default ACCESS_SWITCH).
        dry_run:          Preview without writing to Central.
    """
    if dry_run:
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "dry_run": True}

    client = _get_client()
    global_scope_id = _fetch_global_scope_id(client)

    vi = {
        "vlan": vlan_id,
        "ip_address": ip_address,
        "helper_address": helper_address,
        "dhcp": dhcp,
    }

    try:
        _push_vlan_interface(client, vi, device_scope_id, global_scope_id, persona)
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "pushed": True, "errors": []}
    except Exception as exc:
        return {"vlan_id": vlan_id, "device_scope_id": device_scope_id, "pushed": False, "errors": [str(exc)]}


# ---------------------------------------------------------------------------
# WRITE — Hostname
# ---------------------------------------------------------------------------


@mcp.tool()
def set_hostname(
    device_scope_id: str,
    hostname: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Set the hostname alias on a device in New Central.

    Args:
        device_scope_id: The device's numeric scope-id (use find_device or list_scopes).
        hostname:        The hostname to assign.
        dry_run:         Preview without writing to Central.
    """
    if dry_run:
        return {"device_scope_id": device_scope_id, "hostname": hostname, "dry_run": True}

    client = _get_client()
    try:
        client.post(
            "/network-config/v1/aliases/sys_host_name",
            params={"view-type": "LOCAL", "scope-id": device_scope_id},
            data={
                "name": "sys_host_name",
                "type": "ALIAS_HOSTNAME",
                "default-value": {
                    "hostname-value": {"hostname": hostname}
                },
            },
        )
        return {"device_scope_id": device_scope_id, "hostname": hostname, "set": True, "errors": []}
    except Exception as exc:
        return {"device_scope_id": device_scope_id, "hostname": hostname, "set": False, "errors": [str(exc)]}


# ---------------------------------------------------------------------------
# WRITE — Device profiles
# ---------------------------------------------------------------------------


@mcp.tool()
def push_aruba_device_profiles(dry_run: bool = False) -> dict[str, Any]:
    """Ensure the four standard Aruba LLDP device profiles exist at the library level.

    Creates (or updates) arubaAP, arubaGW, arubaSW, arubaAOS profiles with their
    LLDP match rules, port profiles, and scope-maps. Safe to re-run — idempotent.

    Args:
        dry_run: Preview without writing to Central.
    """
    if dry_run:
        return {"profiles": [p["name"] for p in ARUBA_DEVICE_PROFILES], "dry_run": True}

    client = _get_client()
    creds_path = os.environ.get("CREDS_PATH", "config/credentials.yaml")
    _, target_ctx = build_account_contexts(creds_path)
    target_ctx.central_client = client

    _ensure_device_profiles(client, target_ctx)
    return {
        "profiles": [p["name"] for p in ARUBA_DEVICE_PROFILES],
        "pushed": True,
    }


# ---------------------------------------------------------------------------
# WRITE — SSIDs
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ssids() -> list[dict[str, Any]]:
    """Return all wlan-ssid objects from Aruba New Central."""
    return _list(_get_client())


@mcp.tool()
def get_scope_maps(resource_filter: str | None = None) -> list[dict[str, Any]]:
    """Return all scope-map entries from Aruba New Central.

    Args:
        resource_filter: Optional string to filter results by resource name (e.g. 'wlan-ssids/lab').
    """
    result = _get_client().get("/network-config/v1/scope-maps")
    items: list[dict[str, Any]] = result.get("scope-map", result.get("items", []))
    if resource_filter:
        items = [i for i in items if resource_filter in i.get("resource", "")]
    return items


@mcp.tool()
def get_ssid(ssid_name: str) -> dict[str, Any] | None:
    """Fetch the current configuration for a single SSID.

    Returns the SSID config dict, or None if not found.
    """
    return _get(_get_client(), ssid_name)


@mcp.tool()
def build_underlay_ssid(
    ssid_name: str,
    vlan_ids: list[str],
    scope_id: str,
    persona: str = "CAMPUS_AP",
    opmode: str = "ENHANCED_OPEN",
    wpa_passphrase: str | None = None,
    wpa3_transition: bool = True,
    rf_band: str = "24GHZ_5GHZ",
    hide_ssid: bool = False,
    max_clients: int = 1024,
    client_isolation: bool = False,
    dmo_enable: bool = True,
    dmo_channel_threshold: int = 90,
    dmo_clients_threshold: int = 6,
    inactivity_timeout: int = 1000,
    dtim_period: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a bridge mode (underlay) SSID in Aruba New Central and scope-map it to a device persona.

    Also known as: underlay SSID, bridge mode SSID, non-tunneled SSID.


    BEFORE calling this tool, confirm ALL of the following with the user
    using plain language (not API terms):

    1. ssid_name — the SSID name to broadcast. (Required, no default.)

    2. vlan_ids — list of VLAN ID strings, e.g. ["200"] or ["200","201"].
       Ask: "Which VLAN ID(s) should this SSID use?"

    3. scope_id — resolved from the user's answer to "where should this apply":
       - "Everywhere" / "org-wide" / "all APs" → call get_global_scope_id()
       - A site name or group name → call list_scopes(), find the matching
         scope_name, use its scope_id.
       Ask: "Should this apply everywhere, or to a specific site or group?"

    4. persona — resolved from "which devices":
       - "Access Points" / "APs" / unspecified → CAMPUS_AP (default)
       - "Gateways"                            → MOBILITY_GW
       - "Access Switch"                       → ACCESS_SWITCH
       - "Aggregation Switch"                  → AGG_SWITCH
       - "Core Switch"                         → CORE_SWITCH

    5. opmode — resolved from "what security":
       - "Open" / none specified               → ENHANCED_OPEN (default)
       - "WPA3 with support for older devices" → WPA3_SAE, wpa3_transition=True
       - "WPA3 only"                           → WPA3_SAE, wpa3_transition=False
       - "WPA2" / "pre-shared key" / "PSK"    → WPA2_PSK
       If WPA3_SAE or WPA2_PSK: ask for wpa_passphrase if not provided.

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, persona, created, scope_mapped, errors.
    """
    return _build(
        _get_client(),
        ssid_name=ssid_name,
        vlan_ids=vlan_ids,
        scope_id=scope_id,
        persona=persona,
        opmode=opmode,
        rf_band=rf_band,
        hide_ssid=hide_ssid,
        max_clients=max_clients,
        wpa_passphrase=wpa_passphrase,
        wpa3_transition=wpa3_transition,
        client_isolation=client_isolation,
        dmo_enable=dmo_enable,
        dmo_channel_threshold=dmo_channel_threshold,
        dmo_clients_threshold=dmo_clients_threshold,
        inactivity_timeout=inactivity_timeout,
        dtim_period=dtim_period,
        dry_run=dry_run,
    )


@mcp.tool()
def list_gw_clusters() -> list[dict[str, Any]]:
    """Return all gateway clusters available for tunneled (overlay) SSIDs."""
    return _get_mcp_client().get_gw_clusters()


@mcp.tool()
def build_overlay_ssid(
    ssid_name: str,
    vlan_ids: list[str],
    scope_id: str,
    cluster_name: str,
    cluster_scope_id: str,
    opmode: str = "ENHANCED_OPEN",
    rf_band: str = "BAND_ALL",
    wpa_passphrase: str | None = None,
    wpa3_transition: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a tunneled (overlay) SSID that forwards client traffic through a Mobility Gateway.

    Also known as: overlay SSID, tunneled SSID, GRE tunnel SSID.


    BEFORE calling this tool, confirm the following with the user:

    1. ssid_name — the SSID name to broadcast.

    2. vlan_ids — list of VLAN ID strings, e.g. ["200"].

    3. scope_id — a Device Group scope-id (NOT global — overlay WLANs require device group scope).
       Call list_scopes() to find available device groups.

    4. cluster_name + cluster_scope_id — the gateway cluster to tunnel through.
       Call list_gw_clusters() to discover available clusters.

    5. opmode — security mode (same options as build_underlay_ssid).

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, cluster_name,
        created, overlay_created, scope_mapped, errors.
    """
    return _build_overlay(
        _get_client(),
        ssid_name=ssid_name,
        vlan_ids=vlan_ids,
        scope_id=scope_id,
        cluster_name=cluster_name,
        cluster_scope_id=cluster_scope_id,
        opmode=opmode,
        rf_band=rf_band,
        wpa_passphrase=wpa_passphrase,
        wpa3_transition=wpa3_transition,
        dry_run=dry_run,
    )


@mcp.tool()
def create_allow_all_role(
    role_name: str,
    scope_id: str,
    persona: str = "CAMPUS_AP",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a wireless permit-all role in Aruba New Central and scope-map it.

    When a user asks to "create a role with all access" alongside an SSID,
    reuse the same WHERE and DEVICE TYPE answers from the SSID step.

    Args:
        role_name: Name of the role (typically matches the SSID name).
        scope_id:  Numeric scope-id — resolved from site/group name or global.
        persona:   Device type — CAMPUS_AP (default) | MOBILITY_GW |
                   ACCESS_SWITCH | AGG_SWITCH | CORE_SWITCH.
        dry_run:   Preview actions without writing to Central.

    Returns:
        Dict with keys: role_name, created, scope_mapped, errors.
    """
    return _create_role(_get_client(), role_name=role_name, scope_id=scope_id, persona=persona, dry_run=dry_run)


@mcp.tool()
def delete_underlay_ssid(
    ssid_name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an underlay SSID from Aruba New Central.

    NOTE: The auto-created default role with the same name is NOT removed —
    delete it separately if needed.

    Args:
        ssid_name: Name of the SSID to delete.
        dry_run:   Log the action without calling the delete API.

    Returns:
        Dict with keys: ssid_name, deleted, errors.
    """
    return _delete(_get_client(), ssid_name=ssid_name, dry_run=dry_run)


# ---------------------------------------------------------------------------
# TROUBLESHOOTING — shared helpers
# ---------------------------------------------------------------------------

_CX_TROUBLESHOOTING_BASE = "/network-troubleshooting/v1alpha1/cx"
_POLL_INTERVAL = 5   # seconds between polling attempts
_POLL_MAX = 12       # up to ~60 s total


def _cx_poll(client: "CentralClient", serial: str, operation: str, task_id: str) -> dict[str, Any]:
    """Poll an async troubleshooting task until COMPLETED/FAILED or timeout."""
    endpoint = f"{_CX_TROUBLESHOOTING_BASE}/{serial}/{operation}/async-operations/{task_id}"
    for _ in range(_POLL_MAX):
        time.sleep(_POLL_INTERVAL)
        try:
            result = client.get(endpoint)
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}
        status = result.get("status", "")
        if status in ("COMPLETED", "FAILED"):
            return result
    return result  # return last response even if still running


def _troubleshoot_poll(client: "CentralClient", poll_url: str) -> dict[str, Any]:
    """Poll a generic async-operations URL until COMPLETED/FAILED or timeout (~60 s)."""
    for _ in range(_POLL_MAX):
        time.sleep(_POLL_INTERVAL)
        try:
            result = client.get(poll_url)
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}
        status = result.get("status", "")
        if status in ("COMPLETED", "FAILED"):
            return result
    return result


def _troubleshoot_async(
    client: "CentralClient",
    endpoint: str,
    payload: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """POST an async troubleshooting request, then poll to completion.

    Returns the final async-operations result dict, with errors appended.
    """
    try:
        resp = client._request("POST", endpoint, json=payload)
        if resp.status_code not in (200, 201, 202):
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            errors.append(f"HTTP {resp.status_code}: {body}")
            return {"status": None, "errors": errors}

        # Extract task_id from Location header or response body
        location = resp.headers.get("Location", "") or resp.json().get("location", "")
        task_id = location.rstrip("/").split("/")[-1]
        # Build poll URL: same base path + /async-operations/{task_id}
        poll_url = f"{endpoint}/async-operations/{task_id}"
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}

    result = _troubleshoot_poll(client, poll_url)
    result["errors"] = errors
    return result


@mcp.tool()
def cx_ping(
    serial_number: str,
    destination: str,
    count: int | None = None,
    packet_size: int | None = None,
    vrf_name: str | None = None,
    use_management_interface: bool | None = None,
) -> dict[str, Any]:
    """Ping a destination from a CX switch and return the result.

    Submits the ping via POST /network-troubleshooting/v1alpha1/cx/{serial}/ping,
    then polls the async-operations endpoint until COMPLETED or FAILED (up to ~60 s).

    Args:
        serial_number:           CX switch serial number.
        destination:             Destination IP address or hostname.
        count:                   Number of ping packets (1–100, default 5 on device).
        packet_size:             Packet size in bytes.
        vrf_name:                VRF name to use as source for the ping.
        use_management_interface: True to ping via the management interface.

    Returns:
        Dict with the final async-operations result, plus "errors" if any.
    """
    client = _get_client()
    errors: list[str] = []
    payload: dict[str, Any] = {"destination": destination}
    if count is not None:
        payload["count"] = count
    if packet_size is not None:
        payload["packetSize"] = packet_size
    if vrf_name is not None:
        payload["vrfName"] = vrf_name
    if use_management_interface is not None:
        payload["useManagementInterface"] = use_management_interface

    try:
        resp = client._request(
            "POST",
            f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/ping",
            json=payload,
        )
        if resp.status_code != 202:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            errors.append(f"HTTP {resp.status_code}: {body}")
            return {"status": None, "errors": errors}

        location = resp.json().get("location", "")
        task_id = location.split("/")[-1]
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}

    result = _cx_poll(client, serial_number, "ping", task_id)
    result["errors"] = errors
    return result


@mcp.tool()
def cx_traceroute(
    serial_number: str,
    destination: str,
    vrf_name: str | None = None,
    use_management_interface: bool | None = None,
) -> dict[str, Any]:
    """Run a traceroute to a destination from a CX switch and return the result.

    Submits via POST /network-troubleshooting/v1alpha1/cx/{serial}/traceroute,
    then polls until COMPLETED or FAILED (up to ~60 s).

    Args:
        serial_number:           CX switch serial number.
        destination:             Destination IP address or hostname.
        vrf_name:                VRF name to use as source.
        use_management_interface: True to traceroute via the management interface.

    Returns:
        Dict with the final async-operations result, plus "errors" if any.
    """
    client = _get_client()
    errors: list[str] = []
    payload: dict[str, Any] = {"destination": destination}
    if vrf_name is not None:
        payload["vrfName"] = vrf_name
    if use_management_interface is not None:
        payload["useManagementInterface"] = use_management_interface

    try:
        resp = client._request(
            "POST",
            f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/traceroute",
            json=payload,
        )
        if resp.status_code != 202:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            errors.append(f"HTTP {resp.status_code}: {body}")
            return {"status": None, "errors": errors}

        location = resp.json().get("location", "")
        task_id = location.split("/")[-1]
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}

    result = _cx_poll(client, serial_number, "traceroute", task_id)
    result["errors"] = errors
    return result


@mcp.tool()
def cx_show(
    serial_number: str,
    commands: list[str],
) -> dict[str, Any]:
    """Run one or more 'show' commands on a CX switch and return the output.

    All commands must start with 'show ' (max 20 per call).
    Submits via POST /network-troubleshooting/v1alpha1/cx/{serial}/showCommands,
    then polls until COMPLETED or FAILED (up to ~60 s).

    Args:
        serial_number: CX switch serial number.
        commands:      List of show commands, e.g. ["show version", "show ip route"].

    Returns:
        Dict with the final async-operations result (includes per-command output),
        plus "errors" if any.
    """
    client = _get_client()
    errors: list[str] = []

    if not commands:
        return {"status": None, "errors": ["commands list cannot be empty"]}
    if len(commands) > 20:
        return {"status": None, "errors": [f"commands list cannot exceed 20 items (got {len(commands)})"]}
    for i, cmd in enumerate(commands):
        if not cmd.strip().lower().startswith("show "):
            return {"status": None, "errors": [f"Command {i} must start with 'show ': '{cmd}'"]}

    try:
        resp = client._request(
            "POST",
            f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/showCommands",
            json={"commands": commands},
        )
        if resp.status_code != 202:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            errors.append(f"HTTP {resp.status_code}: {body}")
            return {"status": None, "errors": errors}

        location = resp.json().get("location", "")
        task_id = location.split("/")[-1]
    except Exception as exc:
        errors.append(str(exc))
        return {"status": None, "errors": errors}

    result = _cx_poll(client, serial_number, "showCommands", task_id)
    result["errors"] = errors
    return result


# ── GreenLake Platform (GLP) ─────────────────────────────────────────────────


@mcp.tool()
def list_glp_devices(
    limit: int = 100,
    filter: str | None = None,
) -> dict[str, Any]:
    """List devices in the GLP workspace inventory.

    Returns all hardware registered to the workspace, including warranty,
    subscription state, and lifecycle fields. Complements list_devices (which
    shows Central-managed devices) with GLP ownership and licensing data.

    Args:
        limit:  Maximum number of devices to return (default 100).
        filter: OData filter string, e.g. "serial eq 'SG30LMR164'".

    Returns:
        Dict with keys: items (list of device records), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_devices(limit=limit, filter=filter)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def get_glp_device(serial_number: str) -> dict[str, Any]:
    """Fetch a single device from GLP by serial number.

    Args:
        serial_number: Device serial number.

    Returns:
        Dict with keys: device (record or None), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        device = glp.get_device(serial_number)
        return {"device": device, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"device": None, "errors": errors}


@mcp.tool()
def list_glp_subscriptions(limit: int = 100) -> dict[str, Any]:
    """List subscriptions (license keys) in the GLP workspace.

    Shows license type, assigned device, quantity, and expiry date.

    Args:
        limit: Maximum number of subscriptions to return (default 100).

    Returns:
        Dict with keys: items (list of subscription records), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_subscriptions(limit=limit)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def get_glp_subscription(subscription_id: str) -> dict[str, Any]:
    """Fetch a single GLP subscription by its ID.

    Args:
        subscription_id: Subscription UUID or key.

    Returns:
        Dict with keys: subscription (record or None), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        sub = glp.get_subscription(subscription_id)
        return {"subscription": sub, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"subscription": None, "errors": errors}


@mcp.tool()
def list_glp_users(limit: int = 300) -> dict[str, Any]:
    """List users who have access to the GLP workspace.

    Args:
        limit: Maximum number of users to return (default 300).

    Returns:
        Dict with keys: items (list of user records), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_users(limit=limit)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def list_glp_audit_logs(
    limit: int = 100,
    category: str | None = None,
) -> dict[str, Any]:
    """List GLP audit log entries for the workspace.

    Useful for security review and compliance — shows who did what and when.

    Args:
        limit:    Maximum number of entries to return (default 100).
        category: Filter by category, e.g. "USER_MANAGEMENT", "DEVICE_MANAGEMENT".

    Returns:
        Dict with keys: items (list of log entries), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_audit_logs(limit=limit, category=category)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool()
def glp_assign_subscription(
    serial_number: str,
    subscription_key: str,
) -> dict[str, Any]:
    """Assign a GLP subscription (license) to a device.

    Args:
        serial_number:    Device serial number.
        subscription_key: Subscription key or license type to assign.

    Returns:
        Dict with keys: result (API response), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        result = glp.assign_subscription(serial_number, subscription_key)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool()
def glp_add_device(
    serial_number: str,
    mac_address: str | None = None,
) -> dict[str, Any]:
    """Add a device to the GLP workspace and wait for the task to complete.

    Submits the add request (async 202) then polls until the task finishes
    (up to 5 minutes). Use get_glp_device to confirm afterwards.

    Args:
        serial_number: Device serial number.
        mac_address:   Optional MAC address (required for some device types).

    Returns:
        Dict with keys: task_id, task_result, errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        task_id = glp.add_device(serial_number, mac_address=mac_address)
        task_result = glp.poll_task(task_id)
        return {"task_id": task_id, "task_result": task_result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"task_id": None, "task_result": None, "errors": errors}


@mcp.tool()
def glp_archive_device(serial_number: str) -> dict[str, Any]:
    """Archive a device in GLP (removes it from Central, keeps it in GLP).

    Args:
        serial_number: Device serial number.

    Returns:
        Dict with keys: result (API response), errors.
    """
    glp = _get_glp_client()
    errors: list[str] = []
    try:
        result = glp.archive_device(serial_number)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


# ---------------------------------------------------------------------------
# READ — Inventory
# ---------------------------------------------------------------------------


@mcp.tool()
def list_inventory(
    status: str | None = None,
    device_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List claimed/unprovisioned devices in the Central device inventory.

    Uses GET /network-monitoring/v1alpha1/device-inventory — the same endpoint
    as list_devices but exposes provisioningStatus filtering so you can find
    devices that have been claimed but not yet configured.

    Args:
        status:      Client-side filter by isProvisioned value — "Yes" or "No".
                     Use "No" to find claimed but unprovisioned devices.
        device_type: Filter by deviceType, e.g. "ACCESS_POINT", "SWITCH", "GATEWAY".
        limit:       Maximum number of records to return (default 100).
        offset:      Pagination offset (default 0).

    Returns:
        Dict with keys: items (list of device inventory records), total, errors.
        Each record includes: serialNumber, model, deviceType, status, isProvisioned,
        ipv4, firmwareVersion, siteName, deviceGroupName, scopeId.
    """
    client = _get_client()
    errors: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if device_type:
        params["deviceType"] = device_type
    try:
        result = client.get("/network-monitoring/v1alpha1/device-inventory", params=params)
        items = result.get("items", result.get("devices", []))
        if not isinstance(items, list):
            items = []
        if status:
            items = [d for d in items if d.get("isProvisioned") == status]
        return {"items": items, "total": len(items), "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "total": 0, "errors": errors}


# ---------------------------------------------------------------------------
# READ — Audit Logs (New Central)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_audit_logs(
    start_at: int | None = None,
    end_at: int | None = None,
    limit: int = 100,
    offset: int = 1,
    filter: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """List New Central audit log entries.

    Uses GET /network-services/v1alpha1/audits. Covers config changes, user
    actions, and device operations across the tenant.

    Time range defaults to the last 24 hours if not specified. All times are
    in milliseconds since epoch (not seconds).

    Args:
        start_at: Start of time range in epoch milliseconds.
                  Defaults to 24 hours ago if omitted.
        end_at:   End of time range in epoch milliseconds.
                  Defaults to now if omitted.
        limit:    Maximum entries to return (1–1000, default 100).
        offset:   Pagination starting position (1-based, default 1).
        filter:   OData v4 filter string. Supports 'and' only (no 'or'/'not').
                  Filterable fields: action, description, destination, category,
                  subCategory, destinationName, ipAddress, source.
                  Example: "category eq 'NETWORK_CONFIG' and action eq 'UPDATE'"
        sort:     Comma-separated sort expressions, e.g. "action asc".

    Returns:
        Dict with keys: items (list of audit entries), total, errors.
    """
    client = _get_client()
    errors: list[str] = []
    now_ms = int(time.time() * 1000)
    params: dict[str, Any] = {
        "start-at": start_at if start_at is not None else now_ms - 86_400_000,
        "end-at": end_at if end_at is not None else now_ms,
        "limit": limit,
        "offset": offset,
    }
    if filter:
        params["filter"] = filter
    if sort:
        params["sort"] = sort
    try:
        result = client.get("/network-services/v1alpha1/audits", params=params)
        items = result.get("items", result.get("audits", result.get("logs", [])))
        if not isinstance(items, list):
            items = []
        return {"items": items, "total": result.get("total", len(items)), "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "total": 0, "errors": errors}


@mcp.tool()
def get_audit_log(audit_id: str) -> dict[str, Any]:
    """Fetch a single audit log entry by its audit ID.

    Uses GET /network-services/v1alpha1/audit/{id}.

    Args:
        audit_id: The audit log entry ID to retrieve.

    Returns:
        Dict with keys: audit (the log entry dict), errors.
    """
    client = _get_client()
    errors: list[str] = []
    try:
        result = client.get(f"/network-services/v1alpha1/audit/{audit_id}")
        return {"audit": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"audit": None, "errors": errors}


# ---------------------------------------------------------------------------
# WRITE — SSID Update
# ---------------------------------------------------------------------------


@mcp.tool()
def update_ssid(
    ssid_name: str,
    updates: dict[str, Any],
    scope_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update fields on an existing SSID using PATCH.

    Uses PATCH /network-config/v1/wlan-ssids/{name}. Pass only the fields
    you want to change — all other settings remain untouched.

    Common updatable fields:
      enable (bool), opmode (str), wpa-passphrase (str),
      hide-ssid (bool), max-clients-threshold (int),
      rf-band (str: "24GHZ_5GHZ" | "5GHZ_6GHZ" | "BAND_ALL"),
      vlan-id-range (list[str])

    Args:
        ssid_name:  Name of the SSID to update.
        updates:    Dict of field-value pairs to apply.
        scope_id:   Optional scope-id for a scope-specific (LOCAL) override.
                    If omitted, updates the global/shared SSID definition.
        dry_run:    Preview the payload without submitting.

    Returns:
        Dict with keys: ssid_name, scope_id, updates, response, errors.
    """
    client = _get_client()
    errors: list[str] = []
    url_name = quote(ssid_name, safe="")
    endpoint = f"/network-config/v1/wlan-ssids/{url_name}"
    params: dict[str, Any] = {}
    if scope_id:
        params["scope-id"] = scope_id
        params["view-type"] = "LOCAL"

    if dry_run:
        return {
            "dry_run": True,
            "ssid_name": ssid_name,
            "scope_id": scope_id,
            "updates": updates,
            "response": None,
            "errors": [],
        }

    try:
        response = client._request("PATCH", endpoint, json=updates, params=params or None)
        if response.status_code not in (200, 201, 202, 204):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {
                "ssid_name": ssid_name,
                "scope_id": scope_id,
                "updates": updates,
                "response": None,
                "errors": errors,
            }
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {
            "ssid_name": ssid_name,
            "scope_id": scope_id,
            "updates": updates,
            "response": resp_body,
            "errors": errors,
        }
    except Exception as exc:
        errors.append(str(exc))
        return {
            "ssid_name": ssid_name,
            "scope_id": scope_id,
            "updates": updates,
            "response": None,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# WRITE — Per-Device Firmware Upgrade
# ---------------------------------------------------------------------------


@mcp.tool()
def trigger_device_upgrade(
    serial_number: str,
    firmware_version: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Trigger an immediate firmware upgrade on a specific device.

    Uses POST /network-services/v1alpha1/firmware-upgrade (New Central per-device
    upgrade endpoint). This bypasses any group-level compliance policy and targets
    a single device directly.

    Note: set_firmware_compliance is the correct approach for policy-driven upgrades
    across a group or site. Use this tool only when you need to upgrade one device
    immediately, outside of policy.

    Args:
        serial_number:    Device serial number to upgrade.
        firmware_version: Target firmware version string (e.g. "10.16.1030").
        dry_run:          Preview the payload without submitting.

    Returns:
        Dict with keys: serial_number, firmware_version, response, errors.
    """
    client = _get_client()
    errors: list[str] = []
    payload = {"serialNumbers": [serial_number], "firmwareVersion": firmware_version}

    if dry_run:
        return {
            "dry_run": True,
            "serial_number": serial_number,
            "firmware_version": firmware_version,
            "payload": payload,
            "response": None,
            "errors": [],
        }

    try:
        response = client._request(
            "POST", "/network-services/v1alpha1/firmware-upgrade", json=payload
        )
        if response.status_code == 404:
            errors.append(
                "POST /network-services/v1alpha1/firmware-upgrade returned 404 — "
                "endpoint not available on this instance. "
                "Try set_firmware_compliance for policy-driven upgrades."
            )
            return {
                "serial_number": serial_number,
                "firmware_version": firmware_version,
                "response": None,
                "errors": errors,
            }
        if response.status_code not in (200, 201, 202):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {
                "serial_number": serial_number,
                "firmware_version": firmware_version,
                "response": None,
                "errors": errors,
            }
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {
            "serial_number": serial_number,
            "firmware_version": firmware_version,
            "response": resp_body,
            "errors": errors,
        }
    except Exception as exc:
        errors.append(str(exc))
        return {
            "serial_number": serial_number,
            "firmware_version": firmware_version,
            "response": None,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# WRITE — Device Management
# ---------------------------------------------------------------------------


@mcp.tool()
def reboot_device(
    serial_number: str,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Reboot an AP, CX switch, or gateway.

    Routes to the correct network-troubleshooting endpoint based on device type:
      AP      → POST /network-troubleshooting/v1alpha1/aps/{serial}/reboot
      SWITCH  → POST /network-troubleshooting/v1alpha1/cx/{serial}/reboot
      GATEWAY → POST /network-troubleshooting/v1alpha1/gateways/{serial}/reboot

    If device_type is omitted, it is auto-detected from inventory.

    Args:
        serial_number: Serial number of the device to reboot.
        device_type:   "AP", "SWITCH", or "GATEWAY". Auto-detected if omitted.

    Returns:
        Dict with keys: serial_number, device_type, response, errors.
    """
    client = _get_client()
    errors: list[str] = []

    # Auto-detect device type if not provided
    if not device_type:
        device = _get_mcp_client().get_device_by_serial(serial_number)
        if device:
            raw = device.get("deviceType", "")
            if "ACCESS_POINT" in raw or raw == "AP":
                device_type = "AP"
            elif "SWITCH" in raw:
                device_type = "SWITCH"
            elif "GATEWAY" in raw:
                device_type = "GATEWAY"
        if not device_type:
            errors.append(
                f"Could not determine device type for {serial_number}. "
                "Provide device_type explicitly: 'AP', 'SWITCH', or 'GATEWAY'."
            )
            return {"serial_number": serial_number, "device_type": None, "response": None, "errors": errors}

    dt = device_type.upper()
    if dt in ("AP", "ACCESS_POINT"):
        endpoint = f"/network-troubleshooting/v1alpha1/aps/{serial_number}/reboot"
    elif dt in ("SWITCH", "CX"):
        endpoint = f"/network-troubleshooting/v1alpha1/cx/{serial_number}/reboot"
    elif dt in ("GATEWAY", "GW"):
        endpoint = f"/network-troubleshooting/v1alpha1/gateways/{serial_number}/reboot"
    else:
        errors.append(f"Unknown device_type '{device_type}'. Use 'AP', 'SWITCH', or 'GATEWAY'.")
        return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}

    try:
        response = client._request("POST", endpoint, json={})
        if response.status_code not in (200, 201, 202):
            try:
                body = response.json()
            except Exception:
                body = response.text
            errors.append(f"HTTP {response.status_code}: {body}")
            return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"serial_number": serial_number, "device_type": device_type, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}


@mcp.tool()
def assign_device_to_site(
    serial_number: str,
    site_id: str,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Assign or move a device to a different site.

    Tries the following approaches in order:
      1. POST /network-monitoring/v1/sites/{site_id}/devices
      2. POST /monitoring/v1/site/assign  (classic Central)

    Args:
        serial_number: Serial number of the device to move.
        site_id:       Target site ID (use list_sites to find IDs).
        device_type:   Device type hint — "SWITCH", "AP", or "GATEWAY".

    Returns:
        Dict with keys: serial_number, site_id, response, errors.
    """
    client = _get_client()
    errors: list[str] = []

    candidates = [
        ("POST", f"/network-monitoring/v1/sites/{site_id}/devices",
         {"serials": [serial_number]}),
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
            return {
                "serial_number": serial_number,
                "site_id": site_id,
                "endpoint_used": endpoint,
                "response": resp_body,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "site_id": site_id, "response": None, "errors": errors}


@mcp.tool()
def acknowledge_alert(
    alert_id: str,
    action: str = "ACK",
) -> dict[str, Any]:
    """Acknowledge, clear, or resolve an active alert.

    Tries multiple endpoint variants to find what works on this instance.

    Args:
        alert_id: The alert ID to act on (from list_alerts).
        action:   "ACK" (acknowledge), "CLEAR", or "RESOLVE". Defaults to "ACK".

    Returns:
        Dict with keys: alert_id, action, response, errors.
    """
    client = _get_client()
    errors: list[str] = []

    candidates = [
        ("POST", "/network-notifications/v1/alerts/acknowledge",
         {"alert_id": [alert_id], "action": action}),
        ("POST", f"/network-notifications/v1/alerts/{alert_id}/acknowledge",
         {"action": action}),
        ("PATCH", f"/network-notifications/v1/alerts/{alert_id}",
         {"status": action}),
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
            return {
                "alert_id": alert_id,
                "action": action,
                "endpoint_used": endpoint,
                "response": resp_body,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    return {"alert_id": alert_id, "action": action, "response": None, "errors": errors}


@mcp.tool()
def disconnect_client(
    mac_address: str,
    reason: str = "DISCONNECTED_BY_ADMIN",
) -> dict[str, Any]:
    """Force-disconnect a wireless client by MAC address.

    Tries multiple endpoint variants to find what works on this instance.

    Args:
        mac_address: Client MAC address to disconnect (e.g. "aa:bb:cc:dd:ee:ff").
        reason:      Disconnect reason. Defaults to "DISCONNECTED_BY_ADMIN".

    Returns:
        Dict with keys: mac_address, response, errors.
    """
    client = _get_client()
    errors: list[str] = []

    candidates = [
        ("POST", "/device-management/v1/client/disconnect",
         {"mac_addr": mac_address, "disconnect_type": reason}),
        ("POST", f"/network-troubleshooting/v1alpha1/clients/{mac_address}/disconnect",
         {}),
        ("POST", "/network-monitoring/v1/clients/disconnect",
         {"mac_address": mac_address}),
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
            return {
                "mac_address": mac_address,
                "endpoint_used": endpoint,
                "response": resp_body,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    return {"mac_address": mac_address, "response": None, "errors": errors}


# ---------------------------------------------------------------------------
# WRITE — Device Settings
# ---------------------------------------------------------------------------


@mcp.tool()
def update_device_settings(
    serial_number: str,
    settings: dict[str, Any],
    device_scope_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update general device-level metadata or settings.

    Tries the following approaches in order until one succeeds:
      1. PATCH /network-monitoring/v1/devices/{serial}
      2. PATCH /network-config/v1/switch-system/{serial}?viewtype=LOCAL&scope-id={scope_id}
         (requires device_scope_id; skipped if not provided)

    Common updatable settings fields (vary by device type):
      name (str), location (str), notes (str), latitude (float), longitude (float)

    For switch system settings (pass device_scope_id):
      banner (str), contact (str), location (str)

    Args:
        serial_number:   Serial number of the device to update.
        settings:        Dict of field-value pairs to apply.
        device_scope_id: Config-layer scope-id (use find_device). Required for
                         switch-system config path.
        dry_run:         Preview payload without writing.

    Returns:
        Dict with keys: serial_number, settings, endpoint_used, response, errors.
    """
    if dry_run:
        return {
            "dry_run": True,
            "serial_number": serial_number,
            "settings": settings,
            "device_scope_id": device_scope_id,
            "response": None,
            "errors": [],
        }

    client = _get_client()
    errors: list[str] = []

    candidates: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = [
        ("PATCH", f"/network-monitoring/v1/devices/{serial_number}", settings, None),
    ]
    if device_scope_id:
        candidates.append((
            "PATCH",
            f"/network-config/v1/switch-system/{serial_number}",
            settings,
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
            return {
                "serial_number": serial_number,
                "settings": settings,
                "endpoint_used": endpoint,
                "response": resp_body,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "settings": settings, "endpoint_used": None, "response": None, "errors": errors}


# ---------------------------------------------------------------------------
# READ — Device Stats & Health
# ---------------------------------------------------------------------------


@mcp.tool()
def get_device_trends(
    serial_number: str,
    metric: str,
    start_time: str,
    end_time: str,
    site_id: str | None = None,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Fetch time-series utilization trends for an AP or switch.

    Confirmed endpoints:
      AP:     GET /network-monitoring/v1/aps/{serial}/{metric}-utilization-trends
      Switch: GET /network-monitoring/v1/cx/{serial}/{metric}-utilization-trends
              (falls back to /network-monitoring/v1/switches/{serial}/... if 404)

    The filter uses ISO 8601 timestamps:
      "timestamp gt 2024-01-01T00:00:00Z and timestamp lt 2024-01-02T00:00:00Z"

    Args:
        serial_number: Device serial number.
        metric:        Metric to query:
                         AP:     "cpu", "memory", "throughput"
                         Switch: "cpu" or "memory" → hardware-trends (both together),
                                 "throughput" → interface-trends
        start_time:    ISO 8601 start timestamp, e.g. "2024-01-01T00:00:00Z".
        end_time:      ISO 8601 end timestamp, e.g. "2024-01-02T00:00:00Z".
        site_id:       Site ID to include as query param (use list_sites).
        device_type:   "AP", "SWITCH", or "GATEWAY". Auto-detected from inventory
                       if omitted.

    Returns:
        Dict with keys: serial_number, metric, trends, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []

    # Auto-detect device type if not provided
    if not device_type:
        device = _get_mcp_client().get_device_by_serial(serial_number)
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

    # Build candidate endpoint list based on device type and metric.
    # Switch CPU/memory come from hardware-trends (returns both together).
    # AP metrics use {metric}-utilization-trends or throughput-trends.
    dt = (device_type or "").upper()
    m = metric.lower()
    if dt in ("AP", "ACCESS_POINT"):
        if m == "throughput":
            metric_segment = "throughput-trends"
        else:
            metric_segment = f"{m}-utilization-trends"
        candidates = [
            f"/network-monitoring/v1/aps/{serial_number}/{metric_segment}",
        ]
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
        # Unknown type — try AP path then switch path
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
            metric_segment = f"{m}-utilization-trends"
            candidates = [
                f"/network-monitoring/v1/aps/{serial_number}/{metric_segment}",
                f"/network-monitoring/v1/switches/{serial_number}/{metric_segment}",
            ]

    for endpoint in candidates:
        try:
            response = client._request("GET", endpoint, params=params)
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
                data = response.json()
            except Exception:
                data = {}
            return {
                "serial_number": serial_number,
                "metric": metric,
                "trends": data,
                "endpoint_used": endpoint,
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "metric": metric, "trends": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_device_health(
    serial_number: str | None = None,
    device_scope_id: str | None = None,
) -> dict[str, Any]:
    """Fetch config-health or monitoring health state for a device.

    Uses GET /network-config/v1alpha1/config-health/devices (the confirmed runbook
    endpoint for config compliance/health). Also tries monitoring endpoints as fallback.

    Supply serial_number for monitoring lookups; supply device_scope_id to filter
    the config-health response to a single device.

    Args:
        serial_number:   Device serial number. Used to filter monitoring results.
        device_scope_id: Config-layer scope-id to filter config-health result.

    Returns:
        Dict with keys: serial_number, health, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []

    # Try config-health first (confirmed endpoint from runbook)
    try:
        params: dict[str, Any] = {}
        if device_scope_id:
            params["scope-id"] = device_scope_id
        response = client._request("GET", "/network-config/v1alpha1/config-health/devices", params=params or None)
        if response.status_code == 200:
            data = response.json()
            items = data.get("items", data.get("devices", [data] if data else []))
            # If serial given, filter to that device
            if serial_number and isinstance(items, list):
                matches = [i for i in items if i.get("serialNumber", "").lower() == serial_number.lower()]
                items = matches if matches else items
            return {
                "serial_number": serial_number,
                "health": items,
                "endpoint_used": "/network-config/v1alpha1/config-health/devices",
                "errors": errors,
            }
        errors.append(f"config-health: HTTP {response.status_code}")
    except Exception as exc:
        errors.append(f"config-health: {exc}")

    # Fallback: monitoring device record
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
                    return {
                        "serial_number": serial_number,
                        "health": response.json(),
                        "endpoint_used": endpoint,
                        "errors": errors,
                    }
                errors.append(f"HTTP {response.status_code} at {endpoint}")
            except Exception as exc:
                errors.append(str(exc))

    return {"serial_number": serial_number, "health": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_wireless_metrics(
    serial_number: str,
) -> dict[str, Any]:
    """Fetch AP-specific wireless metrics (RF stats, client count, utilization, channel).

    Tries multiple AP monitoring endpoints in order:
      1. GET /network-monitoring/v1/aps/{serial}
      2. GET /network-monitoring/v1/devices/{serial}/wireless-stats
      3. GET /network-monitoring/v1alpha1/aps/{serial}/rf-stats

    Args:
        serial_number: AP serial number.

    Returns:
        Dict with keys: serial_number, metrics, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []

    candidates = [
        f"/network-monitoring/v1/aps/{serial_number}",
        f"/network-monitoring/v1/devices/{serial_number}/wireless-stats",
        f"/network-monitoring/v1alpha1/aps/{serial_number}/rf-stats",
    ]

    for endpoint in candidates:
        try:
            response = client._request("GET", endpoint)
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
                data = response.json()
            except Exception:
                data = {}
            return {"serial_number": serial_number, "metrics": data, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "metrics": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def list_switch_ports(
    serial_number: str,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """List ports/interfaces on a switch with link state, speed, duplex, and VLAN info.

    Uses GET /network-monitoring/v1/switches/{serial}/interfaces (confirmed endpoint).

    Args:
        serial_number: Switch serial number.
        limit:         Max interfaces to return (default 100, fetches all by default).
        offset:        Pagination offset.
        filter:        OData filter string, e.g. "speed eq '1000' and duplex in ('Full')".
        search:        Free-text search string against interface names/descriptions.

    Returns:
        Dict with keys: serial_number, interfaces, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []

    params: dict[str, Any] = {"limit": limit, "offset": offset}
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            data = response.json()
            interfaces = data.get("interfaces", data.get("items", data))
            return {"serial_number": serial_number, "interfaces": interfaces, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "interfaces": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_sle_metrics(
    site_id: str | None = None,
    serial_number: str | None = None,
    duration: str = "3H",
) -> dict[str, Any]:
    """Fetch SLE (Service Level Experience) scores by site or device.

    SLE metrics measure end-user experience quality (connection success, throughput,
    roaming, etc.) aggregated over a time window.

    Tries multiple endpoints in order:
      1. GET /network-monitoring/v1/sle
      2. GET /network-monitoring/v1alpha1/sle

    Args:
        site_id:       Filter by site ID (use list_sites to find IDs).
        serial_number: Filter by device serial number.
        duration:      Time window — e.g. "3H", "1D", "7D". Defaults to "3H".

    Returns:
        Dict with keys: sle, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []

    params: dict[str, Any] = {"duration": duration}
    if site_id:
        params["site_id"] = site_id
    if serial_number:
        params["serial"] = serial_number

    candidates = [
        "/network-monitoring/v1/sle",
        "/network-monitoring/v1alpha1/sle",
    ]

    for endpoint in candidates:
        try:
            response = client._request("GET", endpoint, params=params)
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
                data = response.json()
            except Exception:
                data = {}
            return {"sle": data, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"sle": None, "endpoint_used": None, "errors": errors}


# ---------------------------------------------------------------------------
# WRITE — Switch Port Profiles & Interface Config
# ---------------------------------------------------------------------------


@mcp.tool()
def create_port_profile(
    profile_name: str,
    body: dict[str, Any],
    description: str = "",
    scope_ids: list[str] | None = None,
    persona: str = "ACCESS_SWITCH",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create (or update) a switch port profile and scope-map it.

    Follows the confirmed two-step pattern from the migration pipeline:
      1. POST /network-config/v1/sw-port-profiles/{name}  — creates the shell with description
      2. PUT  /network-config/v1/sw-port-profiles/{name}  — writes the full configuration body
      3. POST /network-config/v1/scope-maps               — for each scope_id provided

    The `body` argument must use the nested structure the API expects.  Example bodies:

    ACCESS port (AP uplink):
      {
        "mode": "AUTO", "enable": true, "routing": false, "dpi-enable": true,
        "lldp": {"mode": "TX_RX"},
        "switchport": {"interface-mode": "ACCESS", "access-vlan": 5},
        "stp": {"enable": true, "admin-edge-port": true, "bpdu-guard": true,
                "bpdu-filter": false, "loop-guard": false, "root-guard": false,
                "rpvst-filter": false, "rpvst-guard": false, "tcn-guard": false, "priority": 6},
        "poe": {"enabled": true, "allocation-method": "USAGE", "priority": "CRITICAL"}
      }

    TRUNK port (switch-to-switch):
      {
        "mode": "AUTO", "enable": true, "routing": false, "dpi-enable": false, "mtu": 9198,
        "lldp": {"mode": "TX_RX"},
        "switchport": {"interface-mode": "TRUNK", "native-vlan": 1},
        "stp": {"enable": true, "admin-edge-port": false, "bpdu-guard": false,
                "bpdu-filter": false, "loop-guard": false, "root-guard": false,
                "rpvst-filter": false, "rpvst-guard": false, "tcn-guard": false, "priority": 6},
        "poe": {"enabled": false}
      }

    Args:
        profile_name: Name of the port profile.
        body:         Full configuration body dict (nested structure — see examples above).
        description:  Human-readable description for the profile shell.
        scope_ids:    List of scope IDs to scope-map this profile to. Pass both the
                      global scope-id and any switch group scope-ids as needed.
        persona:      Device type for all scope-maps (default ACCESS_SWITCH).
        dry_run:      Preview payloads without writing.

    Returns:
        Dict with keys: profile_name, body, scope_ids, errors.
    """
    if dry_run:
        return {
            "dry_run": True,
            "profile_name": profile_name,
            "description": description,
            "body": body,
            "scope_ids": scope_ids or [],
            "errors": [],
        }

    client = _get_client()
    errors: list[str] = []
    encoded_name = quote(profile_name, safe="")

    # Step 1: create the shell
    try:
        client.post(
            f"/network-config/v1/sw-port-profiles/{encoded_name}",
            data={"description": description},
        )
    except Exception as exc:
        resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "duplicate" not in resp_text.lower() and "already exists" not in resp_text.lower():
            errors.append(f"POST (shell): {exc}")

    # Step 2: write the full config body via PUT
    try:
        client.put(f"/network-config/v1/sw-port-profiles/{encoded_name}", data=body)
    except Exception as exc:
        errors.append(f"PUT (body): {exc}")

    # Step 3: scope-map to each provided scope
    for sid in (scope_ids or []):
        try:
            _post_scope_map(client, sid, persona, f"sw-port-profiles/{profile_name}")
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
            if "already exists" not in resp_text.lower():
                errors.append(f"scope_map(scope={sid}): {exc}")

    return {
        "profile_name": profile_name,
        "body": body,
        "scope_ids": scope_ids or [],
        "errors": errors,
    }


@mcp.tool()
def update_port_config(
    serial_number: str,
    interface_name: str,
    updates: dict[str, Any],
    device_scope_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update ethernet interface configuration on a CX switch (device scope).

    Uses PATCH /network-config/v1/ethernet-interfaces/{interface}?viewtype=LOCAL&scope-id={scope_id}.

    Interface names must use slash notation: "1/1/6", "1/1/1", etc.
    These are automatically URL-encoded ("1/1/6" → "1%2F1%2F6") in the request.

    Common updatable fields:
      port-profile (str)       — name of a port profile to apply
      description (str)        — interface description/label
      admin-state (str)        — "UP" or "DOWN"
      vlan-mode (str)          — "ACCESS" or "TRUNK"
      access-vlan (int)        — access VLAN ID
      native-vlan (int)        — native VLAN for trunk
      allowed-vlans (str)      — trunk allowed VLAN range e.g. "1-100,200"
      poe-priority (str)       — "LOW", "HIGH", or "CRITICAL"
      spanning-tree-port-type (str) — "EDGE", "NETWORK", or "NORMAL"

    Args:
        serial_number:   CX switch serial number (for context/logging).
        interface_name:  Interface name, e.g. "1/1/6".
        updates:         Dict of field-value pairs to PATCH.
        device_scope_id: Config-layer scope-id of the device (use find_device).
        dry_run:         Preview payload without writing.

    Returns:
        Dict with keys: serial_number, interface_name, updates, response, errors.
    """
    if dry_run:
        return {
            "dry_run": True,
            "serial_number": serial_number,
            "interface_name": interface_name,
            "updates": updates,
            "device_scope_id": device_scope_id,
            "response": None,
            "errors": [],
        }

    client = _get_client()
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
            return {
                "serial_number": serial_number,
                "interface_name": interface_name,
                "updates": updates,
                "response": None,
                "errors": errors,
            }
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {
            "serial_number": serial_number,
            "interface_name": interface_name,
            "updates": updates,
            "response": resp_body,
            "errors": errors,
        }
    except Exception as exc:
        errors.append(str(exc))
        return {
            "serial_number": serial_number,
            "interface_name": interface_name,
            "updates": updates,
            "response": None,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# READ — Switch monitoring
# ---------------------------------------------------------------------------


@mcp.tool()
def get_switch_details(serial_number: str) -> dict[str, Any]:
    """Fetch full monitoring details for a switch (status, uptime, CPU, memory, VLANs).

    Uses GET /network-monitoring/v1/switches/{serial}.

    Args:
        serial_number: Switch serial number.

    Returns:
        Dict with keys: serial_number, details, endpoint_used, errors.
    """
    client = _get_client()
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            return {"serial_number": serial_number, "details": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "details": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_switch_vlans(
    serial_number: str,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List VLANs active on a switch with status and membership details.

    Uses GET /network-monitoring/v1/switches/{serial}/vlans.

    Args:
        serial_number: Switch serial number.
        limit:         Max VLANs to return (default 100).
        offset:        Pagination offset.
        filter:        OData filter, e.g. "status in ('Up') and voice in ('Disabled')".

    Returns:
        Dict with keys: serial_number, vlans, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            data = response.json()
            vlans = data.get("vlans", data.get("items", data))
            return {"serial_number": serial_number, "vlans": vlans, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "vlans": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_switch_interface_poe(
    serial_number: str,
    site_id: str | None = None,
) -> dict[str, Any]:
    """Fetch PoE state and power draw for all ports on a switch.

    Uses GET /network-monitoring/v1/switches/{serial}/interface-poe.

    Args:
        serial_number: Switch serial number.
        site_id:       Site ID (optional, may be required by some instances).

    Returns:
        Dict with keys: serial_number, poe, endpoint_used, errors.
    """
    client = _get_client()
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            data = response.json()
            poe = data.get("interfacePoe", data.get("items", data))
            return {"serial_number": serial_number, "poe": poe, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "poe": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_switch_interface_trends(
    serial_number: str,
    start_time: str,
    end_time: str,
    site_id: str | None = None,
    interface_id: str | None = None,
    uplink: bool | None = None,
) -> dict[str, Any]:
    """Fetch throughput trends for switch interfaces over a time window.

    Uses GET /network-monitoring/v1/switches/{serial}/interface-trends.
    Filter uses ISO 8601 timestamps: "timestamp gt 2024-01-01T00:00:00Z and timestamp lt 2024-01-02T00:00:00Z"

    Args:
        serial_number: Switch serial number.
        start_time:    ISO 8601 start timestamp, e.g. "2024-01-01T00:00:00Z".
        end_time:      ISO 8601 end timestamp.
        site_id:       Site ID filter.
        interface_id:  Specific interface name/ID to filter to, e.g. "7" or "1/1/6".
        uplink:        True to filter to uplink interfaces only.

    Returns:
        Dict with keys: serial_number, trends, endpoint_used, errors.
    """
    client = _get_client()
    errors: list[str] = []
    params: dict[str, Any] = {
        "filter": f"timestamp gt {start_time} and timestamp lt {end_time}",
    }
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            return {"serial_number": serial_number, "trends": response.json(), "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "trends": None, "endpoint_used": None, "errors": errors}


# ---------------------------------------------------------------------------
# READ — AP sub-resources
# ---------------------------------------------------------------------------


@mcp.tool()
def get_ap_radios(serial_number: str) -> dict[str, Any]:
    """List radios on an AP with band, channel, power, utilization, and mode.

    Uses GET /network-monitoring/v1/aps/{serial}/radios.

    Args:
        serial_number: AP serial number.

    Returns:
        Dict with keys: serial_number, radios, endpoint_used, errors.
    """
    client = _get_client()
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            data = response.json()
            radios = data.get("radios", data.get("items", data))
            return {"serial_number": serial_number, "radios": radios, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "radios": None, "endpoint_used": None, "errors": errors}


@mcp.tool()
def get_ap_ports(serial_number: str) -> dict[str, Any]:
    """List wired ports on an AP with link state, speed, VLAN, and duplex.

    Uses GET /network-monitoring/v1/aps/{serial}/ports.

    Args:
        serial_number: AP serial number.

    Returns:
        Dict with keys: serial_number, ports, endpoint_used, errors.
    """
    client = _get_client()
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
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                errors.append(f"HTTP {response.status_code} at {endpoint}: {body}")
                continue
            data = response.json()
            ports = data.get("ports", data.get("items", data))
            return {"serial_number": serial_number, "ports": ports, "endpoint_used": endpoint, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    return {"serial_number": serial_number, "ports": None, "endpoint_used": None, "errors": errors}


# ---------------------------------------------------------------------------
# TROUBLESHOOTING — PoE bounce, port bounce, cable test (CX / AOS-S / Gateway)
# ---------------------------------------------------------------------------


def _device_type_for_troubleshoot(serial_number: str, device_type: str | None) -> str | None:
    """Resolve device type string to 'cx', 'aos-s', or 'gateways' for troubleshooting URLs."""
    if not device_type:
        device = _get_mcp_client().get_device_by_serial(serial_number)
        if device:
            raw = device.get("deviceType", "")
            if "ACCESS_POINT" in raw or raw == "AP":
                return None  # APs don't support these ops
            elif "SWITCH" in raw:
                device_type = "SWITCH"
            elif "GATEWAY" in raw:
                device_type = "GATEWAY"

    dt = (device_type or "").upper()
    if dt in ("CX", "SWITCH"):
        return "cx"
    if dt in ("AOS-S", "AOSS", "AOS_S"):
        return "aos-s"
    if dt in ("GATEWAY", "GW"):
        return "gateways"
    # Default to cx for unknown switch types
    return "cx"


@mcp.tool()
def poe_bounce(
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Bounce (power-cycle) PoE on one or more switch or gateway ports.

    Submits via POST /network-troubleshooting/v1/{device-type}/{serial}/poeBounce,
    then polls until COMPLETED or FAILED (up to ~60 s).

    Port name format varies by device type:
      CX switch:  "1/1/1", "1/1/2"
      AOS-S:      "1", "2"
      Gateway:    "GE 0/0/0", "GE 0/0/1"

    Args:
        serial_number: Device serial number.
        ports:         List of port names to bounce.
        device_type:   "CX", "AOS-S", or "GATEWAY". Auto-detected if omitted.

    Returns:
        Dict with the final async-operations result, plus "errors".
    """
    client = _get_client()
    errors: list[str] = []
    dtype = _device_type_for_troubleshoot(serial_number, device_type)
    if dtype is None:
        errors.append("PoE bounce is not supported on Access Points.")
        return {"status": None, "errors": errors}

    endpoint = f"/network-troubleshooting/v1/{dtype}/{serial_number}/poeBounce"
    return _troubleshoot_async(client, endpoint, {"ports": ports}, errors)


@mcp.tool()
def port_bounce(
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Bounce (link-reset) one or more switch or gateway ports.

    Submits via POST /network-troubleshooting/v1/{device-type}/{serial}/portBounce,
    then polls until COMPLETED or FAILED (up to ~60 s).

    Port name format varies by device type:
      CX switch:  "1/1/1", "1/1/2"
      AOS-S:      "1", "2"
      Gateway:    "GE 0/0/0", "GE 0/0/1"

    Args:
        serial_number: Device serial number.
        ports:         List of port names to bounce.
        device_type:   "CX", "AOS-S", or "GATEWAY". Auto-detected if omitted.

    Returns:
        Dict with the final async-operations result, plus "errors".
    """
    client = _get_client()
    errors: list[str] = []
    dtype = _device_type_for_troubleshoot(serial_number, device_type)
    if dtype is None:
        errors.append("Port bounce is not supported on Access Points.")
        return {"status": None, "errors": errors}

    endpoint = f"/network-troubleshooting/v1/{dtype}/{serial_number}/portBounce"
    return _troubleshoot_async(client, endpoint, {"ports": ports}, errors)


@mcp.tool()
def cable_test(
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Run a cable/TDR test on one or more switch ports.

    Submits via POST /network-troubleshooting/v1/{device-type}/{serial}/cableTest,
    then polls until COMPLETED or FAILED (up to ~60 s).

    Supported on CX and AOS-S switches only (not gateways).

    Port name format:
      CX switch:  "1/1/1", "1/1/2"
      AOS-S:      "1", "2"

    Args:
        serial_number: Switch serial number.
        ports:         List of port names to test.
        device_type:   "CX" or "AOS-S". Auto-detected if omitted (defaults to "cx").

    Returns:
        Dict with the final async-operations result, plus "errors".
    """
    client = _get_client()
    errors: list[str] = []
    dtype = _device_type_for_troubleshoot(serial_number, device_type)
    if dtype == "gateways":
        errors.append("Cable test is not supported on gateways.")
        return {"status": None, "errors": errors}
    if dtype is None:
        errors.append("Cable test is not supported on Access Points.")
        return {"status": None, "errors": errors}

    endpoint = f"/network-troubleshooting/v1/{dtype}/{serial_number}/cableTest"
    return _troubleshoot_async(client, endpoint, {"ports": ports}, errors)


if __name__ == "__main__":
    mcp.run()
