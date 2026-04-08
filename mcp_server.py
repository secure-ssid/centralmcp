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

Before calling build_underlay_ssid or create_allow_all_role, ALWAYS confirm
these four things with the user if they haven't been specified:

  1. WHERE to assign (scope):
       - "Org-wide / global" → call get_global_scope_id() automatically
       - "A specific site or device group" → call list_scopes() and present
         the names so the user can pick; use the matching scope-id
       Ask: "Where should this apply — org-wide, or a specific site/group?"

  2. WHAT type of device (persona):
       CAMPUS_AP      — Access Points (most common for wireless SSIDs)
       MOBILITY_GW    — Gateways
       ACCESS_SWITCH  — CX Access switches
       AGG_SWITCH     — CX Aggregation switches
       CORE_SWITCH    — CX Core switches
       Ask: "What device type? (Access Points / Gateways / Access Switch / etc.)"
       Default to CAMPUS_AP if the user says "APs" or "wireless" or doesn't specify.

  3. SECURITY (for build_underlay_ssid):
       - Open/OWE → opmode=ENHANCED_OPEN (default)
       - WPA3 with legacy client support → opmode=WPA3_SAE, wpa3_transition=True
       - WPA3 only → opmode=WPA3_SAE, wpa3_transition=False
       - WPA2 PSK → opmode=WPA2_PSK
       Ask: "What security? Open, WPA3 (with or without legacy support), or WPA2-PSK?"
       If WPA3_SAE or WPA2_PSK: ask for the passphrase if not provided.

  4. VLAN(s) (for build_underlay_ssid):
       Ask: "Which VLAN ID(s) should this SSID use?"

  For create_allow_all_role:
     - role_name defaults to the SSID name (Central auto-creates it, but this
       explicitly scope-maps it). Confirm with user or use SSID name as default.
     - Uses the same scope_id and persona as the SSID unless told otherwise.
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
    """Return all scopes in this Central account — org, sites, and device groups.

    Each entry has: scope_id, scope_name, scope_type.

    Use this to look up the scope_id when a user specifies a site name or device
    group name rather than a numeric scope-id. Present the results to the user
    as a readable list so they can choose where to apply the SSID or role.
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

    BEFORE calling this tool, confirm ALL of the following with the user:

    1. ssid_name — the SSID name to broadcast. (Required, no default.)

    2. vlan_ids — VLAN ID(s) as a list of strings, e.g. ["200"] or ["200","201"].
       Ask: "Which VLAN ID(s) should this SSID use?"

    3. scope_id — numeric scope-id string for where to apply this SSID.
       Ask: "Should this apply org-wide, or to a specific site or device group?"
       - Org-wide / global → call get_global_scope_id() and use the result.
       - Named site or group → call list_scopes(), show the user the names, let
         them pick, then use the matching scope_id.

    4. persona — device type this SSID applies to.
       Ask: "What type of device? Access Points (CAMPUS_AP), Gateways (MOBILITY_GW),
             or a switch type (ACCESS_SWITCH / AGG_SWITCH / CORE_SWITCH)?"
       Default: CAMPUS_AP (use this if user says "APs", "wireless", or doesn't specify).

    5. opmode — security mode.
       Ask: "What security? Open, WPA3 (with legacy support), WPA3-only, or WPA2-PSK?"
       - Open/OWE           → ENHANCED_OPEN (default)
       - WPA3 + legacy      → WPA3_SAE with wpa3_transition=True
       - WPA3 only          → WPA3_SAE with wpa3_transition=False
       - WPA2-PSK           → WPA2_PSK
       If WPA3_SAE or WPA2_PSK: ask for wpa_passphrase if not already provided.

    Other optional settings (only ask if user brings them up):
      rf_band:               24GHZ_5GHZ (default) | 24GHZ_ONLY | 5GHZ_ONLY | 6GHZ_ONLY
      hide_ssid:             Suppress SSID broadcast (default False).
      max_clients:           Max clients per AP radio (default 1024).
      client_isolation:      Client-to-client isolation (default False).
      dmo_enable:            Dynamic Multicast Optimization (default True).
      dmo_channel_threshold: DMO channel utilization threshold % (default 90).
      dmo_clients_threshold: DMO clients threshold (default 6).
      inactivity_timeout:    Client inactivity timeout seconds (default 1000).
      dtim_period:           DTIM period (default 1).
      dry_run:               Log actions without writing to the API (default False).

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

    When a user asks to "create a role with all access" alongside an SSID:
      - role_name defaults to the SSID name. Confirm or ask.
      - scope_id should match the SSID scope (reuse what was used for the SSID).
      - persona should match the SSID persona (reuse it).

    If role_name, scope_id, or persona are unknown, ask:
      "Should the role name match the SSID name? Where should it apply, and for
       which device type?"

    Args:
        role_name: Name of the role (typically matches the SSID name).
        scope_id:  Scope-id to map the role to.
        persona:   Device-function persona: CAMPUS_AP (default) | MOBILITY_GW |
                   ACCESS_SWITCH | AGG_SWITCH | CORE_SWITCH.
        dry_run:   Log actions without writing to the API.

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
