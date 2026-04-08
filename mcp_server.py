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
  list_alerts              List active alerts (optionally by site or severity)
  list_events              List events for a device
  get_events_count         Count events for a device
  list_scopes              List all scopes (org, sites, device groups)
  get_global_scope_id      Discover the org-level global scope-id

  WRITE
  -----
  build_underlay_ssid      Create + scope-map an underlay SSID (bridge mode)
  build_overlay_ssid       Create + scope-map an overlay SSID (GRE tunnel via gateway)
  list_gw_clusters         List available gateway clusters for overlay SSIDs
  create_allow_all_role    Create a permit-all wireless role + scope-map it
  delete_underlay_ssid     Delete an underlay SSID
  get_ssid                 Fetch an existing SSID config
  list_ssids               List all SSID objects
  get_scope_maps           List scope-map entries, optionally filtered by resource
  create_vlan              Create an L2 VLAN and scope-map it globally
  create_vlan_interface    Create an L3 VLAN interface (SVI) at device scope
  set_hostname             Set the hostname alias on a device
  push_aruba_device_profiles  Ensure Aruba LLDP device profiles exist at library level

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
from typing import Any

from mcp.server.fastmcp import FastMCP

from pipeline.clients.central_client import CentralClient
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
    """Create an underlay SSID in Aruba New Central and scope-map it to a device persona.

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
    """Return all gateway clusters available for overlay SSID tunneling."""
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
    """Create an overlay SSID that tunnels client traffic through a Mobility Gateway.

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


if __name__ == "__main__":
    mcp.run()
