"""MCP server — Aruba New Central Underlay SSID tools.

Exposes the following tools to Claude (or any MCP client):
  - build_underlay_ssid   Create + scope-map an underlay SSID
  - delete_underlay_ssid  Delete an underlay SSID
  - get_underlay_ssid     Fetch an existing SSID config
  - list_underlay_ssids   List all SSID objects
  - get_global_scope_id   Discover the org-level global scope-id

Credentials are loaded from config/credentials.yaml (or env vars —
see pipeline/config.py).  Set CREDS_PATH env var to override the path.
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
def get_global_scope_id() -> dict[str, Any]:
    """Discover and return the org-level global scope-id for this Central account.

    Use the returned scope_id as the scope_id argument for build_underlay_ssid
    when you want the SSID to apply org-wide.
    """
    client = _get_client()
    scope_id = _fetch_global_scope_id(client)
    return {"scope_id": scope_id}


@mcp.tool()
def build_underlay_ssid(
    ssid_name: str,
    vlan_ids: list[str],
    scope_id: str,
    opmode: str = "ENHANCED_OPEN",
    rf_band: str = "24GHZ_5GHZ",
    hide_ssid: bool = False,
    max_clients: int = 1024,
    wpa_passphrase: str | None = None,
    wpa3_transition: bool = True,
    client_isolation: bool = False,
    dmo_enable: bool = True,
    dmo_channel_threshold: int = 90,
    dmo_clients_threshold: int = 6,
    inactivity_timeout: int = 1000,
    dtim_period: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an underlay SSID in Aruba New Central and scope-map it to the CAMPUS_AP persona.

    REQUIRED parameters — always ask the user if not provided:
      ssid_name:   The SSID name to broadcast.
      vlan_ids:    One or more VLAN IDs (e.g. ["200"]).
      scope_id:    Scope-id for this SSID. If unknown, call get_global_scope_id() first
                   and use the result for org-wide scope.

    SECURITY parameters — ask the user if they mention PSK, WPA, or "secure":
      opmode:        ENHANCED_OPEN (default, open/OWE) | WPA3_SAE | WPA2_PSK.
                     Use WPA3_SAE with wpa3_transition=True for "WPA3 with legacy clients".
      wpa_passphrase: Required when opmode is WPA3_SAE or WPA2_PSK — ask if not provided.
      wpa3_transition: True = WPA3-SAE + WPA2-PSK transition mode (legacy device support).

    OTHER optional parameters:
      rf_band:               24GHZ_5GHZ | 24GHZ_ONLY | 5GHZ_ONLY | 6GHZ_ONLY
      hide_ssid:             Suppress SSID broadcast.
      max_clients:           Max clients per AP radio (default 1024).
      client_isolation:      Enable client-to-client isolation (default False).
      dmo_enable:            Enable Dynamic Multicast Optimization (default True).
      dmo_channel_threshold: DMO channel utilization threshold % (default 90).
      dmo_clients_threshold: DMO clients threshold (default 6).
      inactivity_timeout:    Client inactivity timeout in seconds (default 1000).
      dtim_period:           DTIM period (default 1).
      dry_run:               Log actions without calling any write APIs.

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, created, scope_mapped, errors.
    """
    client = _get_client()
    return _build(
        client,
        ssid_name=ssid_name,
        vlan_ids=vlan_ids,
        scope_id=scope_id,
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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a wireless allow-all role in Aruba New Central and scope-map it to CAMPUS_AP.

    This creates a role with no ACL/policy restrictions — all traffic is permitted.
    Use this after building an underlay SSID when the user asks to "create a role that
    allows all access" or similar.

    The role_name is typically the same as the SSID name (Central auto-creates a default
    role with that name, but this explicitly creates it with permit-all behaviour and
    ensures the scope-map is in place).

    REQUIRED parameters — ask the user if not provided:
      role_name: Name of the role to create (usually matches the SSID name).
      scope_id:  Scope-id to map the role to (use the same scope_id as the SSID).

    Returns:
        Dict with keys: role_name, created, scope_mapped, errors.
    """
    client = _get_client()
    return _create_role(client, role_name=role_name, scope_id=scope_id, dry_run=dry_run)


@mcp.tool()
def delete_underlay_ssid(
    ssid_name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an underlay SSID from Aruba New Central.

    NOTE: The auto-created default role with the same name as the SSID is NOT
    removed by this call — delete it separately if needed.

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
