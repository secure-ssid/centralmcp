"""MCP server — Aruba New Central Underlay SSID tools.

Exposes the following tools to Claude (or any MCP client):
  - build_underlay_ssid    Create + scope-map an underlay SSID
  - create_allow_all_role  Create a permit-all wireless role + scope-map it
  - delete_underlay_ssid   Delete an underlay SSID
  - get_underlay_ssid      Fetch an existing SSID config
  - list_underlay_ssids    List all SSID objects
  - list_scopes            List all scopes (org, sites, device groups) with names + IDs
  - get_global_scope_id    Discover the org-level global scope-id

Credentials are loaded from config/credentials.yaml (or env vars —
see pipeline/config.py).  Set CREDS_PATH env var to override the path.

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
from pipeline.clients.token_manager import TokenManager
from pipeline.config import build_account_contexts
from pipeline.ssid_underlay import (
    build_underlay_ssid as _build,
    create_allow_all_role as _create_role,
    delete_underlay_ssid as _delete,
    get_underlay_ssid as _get,
    list_underlay_ssids as _list,
)
from pipeline.stages.s6_configure import _fetch_global_scope_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mcp = FastMCP("aruba-central-ssid")

# ---------------------------------------------------------------------------
# Shared client (lazy-initialised once per process)
# ---------------------------------------------------------------------------

_central_client: CentralClient | None = None


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


# ---------------------------------------------------------------------------
# Tools
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
        result = client.get("/network-config/v1/scope-maps")
        seen: dict[str, dict] = {}
        for entry in result.get("scope-map", []):
            sid = str(entry.get("scope-id", ""))
            sname = entry.get("scope-name", sid)
            stype = entry.get("scope-type", "")
            if sid and sid not in seen:
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
    client = _get_client()
    scope_id = _fetch_global_scope_id(client)
    return {"scope_id": scope_id}


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
       Ask: "Which devices should get this SSID — Access Points, Gateways,
             or a switch type?"

    5. opmode — resolved from "what security":
       - "Open" / none specified               → ENHANCED_OPEN (default)
       - "WPA3 with support for older devices" → WPA3_SAE, wpa3_transition=True
       - "WPA3 only"                           → WPA3_SAE, wpa3_transition=False
       - "WPA2" / "pre-shared key" / "PSK"    → WPA2_PSK
       Ask: "What security — open, WPA3 (with or without support for older
             devices), or WPA2 with a pre-shared key?"
       If WPA3_SAE or WPA2_PSK: ask for wpa_passphrase if not provided.

    Other optional settings (only ask if user brings them up):
      rf_band:               Radio band — 2.4+5GHz (default) | 2.4GHz only |
                             5GHz only | 6GHz only
      hide_ssid:             Hide SSID from broadcast (default False).
      max_clients:           Max clients per radio (default 1024).
      client_isolation:      Prevent clients talking to each other (default False).
      dmo_enable:            Dynamic Multicast Optimization (default True).
      dmo_channel_threshold: DMO channel utilization % threshold (default 90).
      dmo_clients_threshold: DMO clients threshold (default 6).
      inactivity_timeout:    Client inactivity timeout in seconds (default 1000).
      dtim_period:           DTIM period (default 1).
      dry_run:               Preview actions without writing to Central (default False).

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, persona, created, scope_mapped, errors.
    """
    client = _get_client()
    return _build(
        client,
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
def create_allow_all_role(
    role_name: str,
    scope_id: str,
    persona: str = "CAMPUS_AP",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a wireless permit-all role in Aruba New Central and scope-map it.

    Central automatically creates a default role with the same name as the SSID
    when an SSID is created. This tool explicitly creates/confirms that role with
    permit-all behaviour and ensures the scope-map is in place.

    When a user asks to "create a role with all access" alongside an SSID,
    reuse the answers already given for WHERE and DEVICE TYPE from the SSID step.

    If creating a role standalone (not after an SSID), ask:
      - "What should the role be named?" (default: same as the SSID name)
      - "Where should it apply — everywhere, or a specific site or group?"
        Resolve to scope_id using get_global_scope_id() or list_scopes().
      - "Which devices — Access Points, Gateways, or a switch type?"
        Resolve to persona using the same mapping as build_underlay_ssid.

    Args:
        role_name: Name of the role (typically matches the SSID name).
        scope_id:  Numeric scope-id — resolved from site/group name or global.
        persona:   Device type — CAMPUS_AP (default) | MOBILITY_GW |
                   ACCESS_SWITCH | AGG_SWITCH | CORE_SWITCH.
        dry_run:   Preview actions without writing to Central.

    Returns:
        Dict with keys: role_name, created, scope_mapped, errors.
    """
    client = _get_client()
    return _create_role(client, role_name=role_name, scope_id=scope_id, persona=persona, dry_run=dry_run)


@mcp.tool()
def delete_underlay_ssid(
    ssid_name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an underlay SSID from Aruba New Central.

    NOTE: The auto-created default role with the same name is NOT removed by this
    call — delete it separately if needed.

    Args:
        ssid_name: Name of the SSID to delete.
        dry_run:   Log the action without calling the delete API.

    Returns:
        Dict with keys: ssid_name, deleted, errors.
    """
    client = _get_client()
    return _delete(client, ssid_name=ssid_name, dry_run=dry_run)


@mcp.tool()
def get_underlay_ssid(ssid_name: str) -> dict[str, Any] | None:
    """Fetch the current configuration for a single underlay SSID.

    Returns the SSID config dict, or None if not found.
    """
    client = _get_client()
    return _get(client, ssid_name)


@mcp.tool()
def list_underlay_ssids() -> list[dict[str, Any]]:
    """Return all wlan-ssid objects from Aruba New Central."""
    client = _get_client()
    return _list(client)


if __name__ == "__main__":
    mcp.run()
