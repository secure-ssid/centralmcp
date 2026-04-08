"""Underlay SSID builder for HPE Aruba New Central.

Workflow (per the Configuration APIs runbook v2026.0331):
  Step 1 — Discover the org-level global scope-id (reuses s6_configure logic).
  Step 2 — POST /network-config/v1/wlan-ssids/{essid_name}  (create SSID).
  Step 3 — POST /network-config/v1/scope-maps  (map SSID to CAMPUS_AP persona
            at the requested scope — global by default, or a specific device group).

Notes:
  - forward-mode is always FORWARD_MODE_BRIDGE for underlay.
  - A default role with the same name as the ESSID is auto-created by Central.
  - SSID names with spaces must be %20-encoded in the URL path but left as-is
    in the JSON body fields.
  - Deletion does NOT auto-remove the default role — use delete_underlay_ssid()
    which only deletes the wlan-ssid resource (role cleanup is caller's responsibility).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default SSID body template (all tunable fields exposed as parameters)
# ---------------------------------------------------------------------------

def _build_ssid_body(
    ssid_name: str,
    vlan_ids: list[str],
    *,
    enabled: bool = True,
    opmode: str = "ENHANCED_OPEN",
    rf_band: str = "24GHZ_5GHZ",
    hide_ssid: bool = False,
    max_clients: int = 1024,
    wpa3_transition: bool = True,
    wpa_passphrase: str | None = None,
    client_isolation: bool = False,
    dmo_enable: bool = True,
    dmo_channel_threshold: int = 90,
    dmo_clients_threshold: int = 6,
    inactivity_timeout: int = 1000,
    dtim_period: int = 1,
) -> dict[str, Any]:
    """Return the full WLAN SSID POST body for an underlay SSID.

    wpa_passphrase is required when opmode is WPA3_SAE or WPA2_PSK.
    It maps to personal-security.wpa-passphrase in the API body.
    """
    body: dict[str, Any] = {
        "ssid": ssid_name,
        "enable": enabled,
        "forward-mode": "FORWARD_MODE_BRIDGE",
        "dmo": {
            "enable": dmo_enable,
            "channel-utilization-threshold": dmo_channel_threshold,
            "clients-threshold": dmo_clients_threshold,
        },
        "broadcast-filter-ipv4": "BCAST_FILTER_ARP",
        "broadcast-filter-ipv6": "UCAST_FILTER_RA",
        "optimize-mcast-rate": False,
        "ssid-utf8": True,
        "essid": {
            "use-alias": False,
            "name": ssid_name,
        },
        "advertise-apname": False,
        "disable-on-6ghz-mesh": False,
        "dot11k": True,
        "dtim-period": dtim_period,
        "ftm-responder": False,
        "hide-ssid": hide_ssid,
        "auth-req-thresh": 0,
        "explicit-ageout-client": False,
        "inactivity-timeout": inactivity_timeout,
        "local-probe-req-thresh": 0,
        "max-clients-threshold": max_clients,
        "rf-band": rf_band,
        "rrm-quiet-ie": False,
        "high-throughput": {
            "enable": True,
            "very-high-throughput": True,
        },
        "g-legacy-rates": {
            "basic-rates": ["RATE_12MB", "RATE_24MB"],
            "tx-rates": ["RATE_12MB", "RATE_18MB", "RATE_24MB", "RATE_36MB", "RATE_48MB", "RATE_54MB"],
        },
        "a-legacy-rates": {
            "basic-rates": ["RATE_12MB", "RATE_24MB"],
            "tx-rates": ["RATE_12MB", "RATE_18MB", "RATE_24MB", "RATE_36MB", "RATE_48MB", "RATE_54MB"],
        },
        "high-efficiency": {
            "enable": True,
        },
        "extremely-high-throughput": {
            "enable": True,
            "mlo": False,
            "beacon-protection": False,
        },
        "wmm-cfg": {
            "uapsd": True,
        },
        "advertise-timing": False,
        "opmode": opmode,
        "use-ip-for-calling-station-id": False,
        "called-station-id": {
            "type": "MAC_ADDRESS",
            "include-ssid": False,
        },
        "cloud-auth": False,
        "wpa3-transition-mode-enable": wpa3_transition,
        "denylist": True,
        "max-authentication-failures": 0,
        "enforce-dhcp": False,
        "pan": False,
        "vlan-selector": "VLAN_RANGES",
        "vlan-id-range": vlan_ids,
        "out-of-service": "NONE",
        "client-isolation": client_isolation,
    }
    if wpa_passphrase and opmode in ("WPA3_SAE", "WPA2_PSK"):
        body["personal-security"] = {
            "passphrase-format": "STRING",
            "wpa-passphrase": wpa_passphrase,
        }
    return body


# ---------------------------------------------------------------------------
# Core workflow
# ---------------------------------------------------------------------------

def build_underlay_ssid(
    central_client: Any,
    ssid_name: str,
    vlan_ids: list[str],
    scope_id: str,
    *,
    persona: str = "CAMPUS_AP",
    # Optional overrides
    enabled: bool = True,
    opmode: str = "ENHANCED_OPEN",
    rf_band: str = "24GHZ_5GHZ",
    hide_ssid: bool = False,
    max_clients: int = 1024,
    wpa3_transition: bool = True,
    wpa_passphrase: str | None = None,
    client_isolation: bool = False,
    dmo_enable: bool = True,
    dmo_channel_threshold: int = 90,
    dmo_clients_threshold: int = 6,
    inactivity_timeout: int = 1000,
    dtim_period: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an underlay SSID and scope-map it to the specified persona.

    Args:
        central_client: CentralClient instance with valid credentials.
        ssid_name:      SSID name as it will be broadcast (spaces allowed).
        vlan_ids:       List of VLAN IDs to assign (e.g. ["1000"]).
        scope_id:       Scope-id to map the SSID to. Pass the global scope-id
                        to apply org-wide, or a device-group scope-id for a
                        narrower scope.
        persona:        Device-function persona for the scope-map. Default CAMPUS_AP.
                        Valid values: CAMPUS_AP, MOBILITY_GW, ACCESS_SWITCH,
                        AGG_SWITCH, CORE_SWITCH.
        dry_run:        If True, log actions but do not call any write APIs.

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, persona, created (bool),
        scope_mapped (bool), errors (list[str]).
    """
    url_name = quote(ssid_name, safe="")  # %20-encode spaces for URL path
    result: dict[str, Any] = {
        "ssid_name": ssid_name,
        "vlan_ids": vlan_ids,
        "scope_id": scope_id,
        "persona": persona,
        "created": False,
        "scope_mapped": False,
        "errors": [],
    }

    body = _build_ssid_body(
        ssid_name,
        vlan_ids,
        enabled=enabled,
        opmode=opmode,
        rf_band=rf_band,
        hide_ssid=hide_ssid,
        max_clients=max_clients,
        wpa3_transition=wpa3_transition,
        wpa_passphrase=wpa_passphrase,
        client_isolation=client_isolation,
        dmo_enable=dmo_enable,
        dmo_channel_threshold=dmo_channel_threshold,
        dmo_clients_threshold=dmo_clients_threshold,
        inactivity_timeout=inactivity_timeout,
        dtim_period=dtim_period,
    )

    # ------------------------------------------------------------------
    # Step 2: Create wlan-ssid
    # ------------------------------------------------------------------
    endpoint = f"/network-config/v1/wlan-ssids/{url_name}"

    if dry_run:
        logger.info("[dry-run] Would POST %s with vlan_ids=%s opmode=%s", endpoint, vlan_ids, opmode)
        result["created"] = True  # pretend success for reporting
    else:
        try:
            central_client.post(endpoint, data=body)
            result["created"] = True
            logger.info("Created underlay SSID '%s'", ssid_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("SSID '%s' already exists — skipping create, continuing to scope-map", ssid_name)
                result["created"] = True  # treat as success
            else:
                result["errors"].append(f"create_ssid: {exc}")
                logger.error("Failed to create SSID '%s': %s", ssid_name, exc)
                return result

    # ------------------------------------------------------------------
    # Step 3: Scope-map to persona
    # ------------------------------------------------------------------
    scope_map_body = {
        "scope-map": [
            {
                "scope-name": scope_id,
                "scope-id": int(scope_id),
                "persona": persona,
                "resource": f"wlan-ssids/{ssid_name}",
            }
        ]
    }

    if dry_run:
        logger.info(
            "[dry-run] Would POST /network-config/v1/scope-maps — %s scope=%s resource=wlan-ssids/%s",
            persona, scope_id, ssid_name,
        )
        result["scope_mapped"] = True
    else:
        try:
            central_client.post("/network-config/v1/scope-maps", data=scope_map_body)
            result["scope_mapped"] = True
            logger.info("Scope-mapped SSID '%s' → %s scope-id=%s", ssid_name, persona, scope_id)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "already exists" in resp_text.lower():
                logger.warning("Scope-map for '%s' already exists — skipping", ssid_name)
                result["scope_mapped"] = True
            else:
                result["errors"].append(f"scope_map: {exc}")
                logger.error("Failed to scope-map SSID '%s': %s", ssid_name, exc)

    return result


def create_allow_all_role(
    central_client: Any,
    role_name: str,
    scope_id: str,
    *,
    persona: str = "CAMPUS_AP",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a wireless role that allows all traffic and scope-map it to the specified persona.

    The role is created with:
      - No ACL/policy attached (open permit-all behaviour by default in Central)
      - captive-portal: disabled
      - VLAN derived from the SSID's vlan-selector (Central inherits from SSID)

    Returns dict with keys: role_name, created (bool), scope_mapped (bool), errors (list[str]).
    """
    url_name = quote(role_name, safe="")
    result: dict[str, Any] = {
        "role_name": role_name,
        "created": False,
        "scope_mapped": False,
        "errors": [],
    }

    role_body = {
        "name": role_name,
        "type": "WIRELESS",
        "captive-portal-profile": "disabled",
    }

    endpoint = f"/network-config/v1/roles/{url_name}"

    if dry_run:
        logger.info("[dry-run] Would POST %s (allow-all wireless role)", endpoint)
        result["created"] = True
    else:
        try:
            central_client.post(endpoint, data=role_body)
            result["created"] = True
            logger.info("Created allow-all wireless role '%s'", role_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("Role '%s' already exists — continuing to scope-map", role_name)
                result["created"] = True
            else:
                result["errors"].append(f"create_role: {exc}")
                logger.error("Failed to create role '%s': %s", role_name, exc)
                return result

    # Scope-map role to CAMPUS_AP
    scope_map_body = {
        "scope-map": [
            {
                "scope-name": scope_id,
                "scope-id": int(scope_id),
                "persona": persona,
                "resource": f"roles/{role_name}",
            }
        ]
    }

    if dry_run:
        logger.info(
            "[dry-run] Would POST /network-config/v1/scope-maps — %s scope=%s resource=roles/%s",
            persona, scope_id, role_name,
        )
        result["scope_mapped"] = True
    else:
        try:
            central_client.post("/network-config/v1/scope-maps", data=scope_map_body)
            result["scope_mapped"] = True
            logger.info("Scope-mapped role '%s' → %s scope-id=%s", role_name, persona, scope_id)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "already exists" in resp_text.lower():
                logger.warning("Scope-map for role '%s' already exists — skipping", role_name)
                result["scope_mapped"] = True
            else:
                result["errors"].append(f"scope_map_role: {exc}")
                logger.error("Failed to scope-map role '%s': %s", role_name, exc)

    return result


def delete_underlay_ssid(
    central_client: Any,
    ssid_name: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an underlay SSID.

    NOTE: Deletion does NOT remove the auto-created default role with the same
    name. The caller must delete the role separately if desired.

    Returns dict with keys: ssid_name, deleted (bool), errors (list[str]).
    """
    url_name = quote(ssid_name, safe="")
    result: dict[str, Any] = {
        "ssid_name": ssid_name,
        "deleted": False,
        "errors": [],
    }

    endpoint = f"/network-config/v1/wlan-ssids/{url_name}"

    if dry_run:
        logger.info("[dry-run] Would DELETE %s", endpoint)
        result["deleted"] = True
        return result

    try:
        central_client.delete(endpoint)
        result["deleted"] = True
        logger.info("Deleted underlay SSID '%s'", ssid_name)
    except Exception as exc:
        result["errors"].append(f"delete_ssid: {exc}")
        logger.error("Failed to delete SSID '%s': %s", ssid_name, exc)

    return result


def get_underlay_ssid(
    central_client: Any,
    ssid_name: str,
) -> dict[str, Any] | None:
    """Fetch an existing underlay SSID configuration, or None if not found."""
    url_name = quote(ssid_name, safe="")
    try:
        return central_client.get(f"/network-config/v1/wlan-ssids/{url_name}")
    except Exception as exc:
        resp_status = getattr(getattr(exc, "response", None), "status_code", None)
        if resp_status == 404:
            return None
        logger.warning("get_underlay_ssid('%s') failed: %s", ssid_name, exc)
        return None


def list_underlay_ssids(central_client: Any) -> list[dict[str, Any]]:
    """Return all wlan-ssid objects from Central."""
    try:
        result = central_client.get("/network-config/v1/wlan-ssids")
        # API returns singular "wlan-ssid" key (not plural)
        items = result.get("wlan-ssid", result.get("wlan-ssids", result.get("items", [])))
        return items if isinstance(items, list) else []
    except Exception as exc:
        logger.warning("list_underlay_ssids failed: %s", exc)
        return []
