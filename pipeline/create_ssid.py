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


def build_overlay_ssid(
    central_client: Any,
    ssid_name: str,
    vlan_ids: list[str],
    scope_id: str,
    cluster_name: str,
    cluster_scope_id: str,
    *,
    opmode: str = "ENHANCED_OPEN",
    rf_band: str = "BAND_ALL",
    wpa_passphrase: str | None = None,
    wpa3_transition: bool = True,
    mac_auth_server_group: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an overlay SSID that tunnels client traffic through a Mobility Gateway.

    Args:
        central_client:        CentralClient instance with valid credentials.
        ssid_name:             SSID name to broadcast.
        vlan_ids:              List of VLAN IDs to assign (e.g. ["200"]).
        scope_id:              Device Group scope-id (overlay WLANs cannot use global scope).
        cluster_name:          Name of the gateway cluster to tunnel through.
        cluster_scope_id:      Scope-id of the gateway cluster.
        mac_auth_server_group: If set, creates an AAA profile named after the SSID pointing
                               to this Central NAC server group and attaches MAC auth to the SSID.
        dry_run:               If True, log actions but do not call any write APIs.

    Returns:
        Dict with keys: ssid_name, vlan_ids, scope_id, cluster_name,
        created (bool), overlay_created (bool), scope_mapped (bool),
        aaa_profile_created (bool), errors (list[str]).
    """
    url_name = quote(ssid_name, safe="")
    result: dict[str, Any] = {
        "ssid_name": ssid_name,
        "vlan_ids": vlan_ids,
        "scope_id": scope_id,
        "cluster_name": cluster_name,
        "created": False,
        "overlay_created": False,
        "scope_mapped": False,
        "aaa_profile_created": False,
        "errors": [],
    }

    # ------------------------------------------------------------------
    # Step 1: Create allow-all role first (must exist before SSID references it)
    # ------------------------------------------------------------------
    role_body = {
        "name": ssid_name,
        "utf8": True,
    }
    role_endpoint = f"/network-config/v1/roles/{url_name}"
    if dry_run:
        logger.info("[dry-run] Would POST %s (allow-all wireless role)", role_endpoint)
    else:
        try:
            central_client.post(role_endpoint, data=role_body)
            logger.info("Created allow-all wireless role '%s'", ssid_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("Role '%s' already exists — continuing", ssid_name)
            else:
                result["errors"].append(f"create_role: {exc}")
                logger.error("Failed to create role '%s': %s", ssid_name, exc)

    # Scope-map the role at global scope (CAMPUS_AP + MOBILITY_GW) AND at the
    # device group scope for MOBILITY_GW — gateways only resolve roles scoped
    # to their own device group, not just global.
    from pipeline.stages.s6_configure import _fetch_global_scope_id
    global_scope_id = _fetch_global_scope_id(central_client)
    role_scope_targets = [
        (global_scope_id, "CAMPUS_AP"),
        (global_scope_id, "MOBILITY_GW"),
        (scope_id, "MOBILITY_GW"),  # device group scope — required for GW role resolution
    ]
    for r_scope_id, persona in role_scope_targets:
        for resource in (f"roles/{ssid_name}", f"role-gpids/{ssid_name}"):
            role_scope_map = {
                "scope-map": [
                    {
                        "scope-name": r_scope_id,
                        "scope-id": int(r_scope_id),
                        "persona": persona,
                        "resource": resource,
                    }
                ]
            }
            if dry_run:
                logger.info("[dry-run] Would scope-map %s → %s scope=%s", resource, persona, r_scope_id)
            else:
                try:
                    central_client.post("/network-config/v1/scope-maps", data=role_scope_map)
                    logger.info("Scope-mapped %s → %s scope-id=%s", resource, persona, r_scope_id)
                except Exception as exc:
                    resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
                    if "already exists" in resp_text.lower():
                        logger.warning("Scope-map for %s (%s) already exists — skipping", resource, persona)
                    else:
                        result["errors"].append(f"scope_map_role ({resource}/{persona}): {exc}")
                        logger.error("Failed to scope-map %s (%s): %s", resource, persona, exc)

    # ------------------------------------------------------------------
    # Step 1b: Create AAA profile for MAC auth (if requested)
    # ------------------------------------------------------------------
    aaa_profile_name = ssid_name  # profile name matches SSID name
    if mac_auth_server_group:
        # Step 1b-i: Create macauth server object (required before AAA profile can reference it)
        macauth_endpoint = f"/network-config/v1alpha1/macauth/{quote(aaa_profile_name, safe='')}"
        macauth_payload = {"name": aaa_profile_name}
        if dry_run:
            logger.info("[dry-run] Would POST %s (macauth server object)", macauth_endpoint)
        else:
            try:
                central_client.post(macauth_endpoint, data=macauth_payload)
                logger.info("Created macauth object '%s'", aaa_profile_name)
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
                if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                    logger.warning("macauth object '%s' already exists — continuing", aaa_profile_name)
                else:
                    result["errors"].append(f"create_macauth: {exc}")
                    logger.error("Failed to create macauth object '%s': %s", aaa_profile_name, exc)

        # Step 1b-ii: Create AAA profile referencing the macauth object and server group
        aaa_payload = {
            "name": aaa_profile_name,
            "authentication": {
                "mac-auth": aaa_profile_name,
                "mac-default-role": ssid_name,
                "macauth-server-group": mac_auth_server_group,
            },
        }
        aaa_endpoint = f"/network-config/v1alpha1/aaa-profile/{quote(aaa_profile_name, safe='')}"
        if dry_run:
            logger.info("[dry-run] Would POST %s (AAA profile, server-group=%s)", aaa_endpoint, mac_auth_server_group)
            result["aaa_profile_created"] = True
        else:
            try:
                central_client.post(aaa_endpoint, data=aaa_payload)
                result["aaa_profile_created"] = True
                logger.info("Created AAA profile '%s' → server-group '%s'", aaa_profile_name, mac_auth_server_group)
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
                if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                    logger.warning("AAA profile '%s' already exists — continuing", aaa_profile_name)
                    result["aaa_profile_created"] = True
                else:
                    result["errors"].append(f"create_aaa_profile: {exc}")
                    logger.error("Failed to create AAA profile '%s': %s", aaa_profile_name, exc)

    # Build WLAN body with overlay-specific overrides
    body = _build_ssid_body(
        ssid_name,
        vlan_ids,
        opmode=opmode,
        rf_band=rf_band,
        wpa_passphrase=wpa_passphrase,
        wpa3_transition=wpa3_transition if opmode != "ENHANCED_OPEN" else False,
        dmo_enable=False,
    )
    body["forward-mode"] = "FORWARD_MODE_L2"
    body["out-of-service"] = "TUNNEL_DOWN"
    body["cluster-preemption"] = False
    body["type"] = "EMPLOYEE"
    body["default-role"] = ssid_name
    if mac_auth_server_group:
        body["mac-authentication"] = True
        body["auth-server-group"] = mac_auth_server_group
        body["acct-server-group"] = mac_auth_server_group
        body["cloud-auth"] = True
        body["radius-accounting"] = True
        body["radius-interim-accounting-interval"] = 10

    # ------------------------------------------------------------------
    # Step 1c: Create allow-all security policy + add to policy group + scope-map
    # (Required so the role shows "Referenced By 1 policy" and GW can enforce it)
    # ------------------------------------------------------------------
    policy_endpoint = f"/network-config/v1alpha1/policies/{url_name}"
    policy_payload = {
        "name": ssid_name,
        "type": "POLICY_TYPE_SECURITY",
        "security-policy": {
            "type": "SECURITY_POLICY_TYPE_DEFAULT",
            "policy-rule": [
                {
                    "position": 1,
                    "description": "Allow All",
                    "condition": {
                        "type": "CONDITION_DEFAULT",
                        "rule-type": "RULE_ANY",
                        "source": {"type": "ADDRESS_ROLE", "role": ssid_name},
                        "destination": {"type": "ADDRESS_ANY"},
                    },
                    "action": {"type": "ACTION_ALLOW"},
                }
            ],
        },
    }
    if dry_run:
        logger.info("[dry-run] Would POST %s (allow-all security policy)", policy_endpoint)
        logger.info("[dry-run] Would PATCH policy-groups to add '%s'", ssid_name)
        logger.info("[dry-run] Would scope-map policies/%s → CAMPUS_AP + MOBILITY_GW", ssid_name)
    else:
        try:
            central_client.post(policy_endpoint, data=policy_payload)
            logger.info("Created allow-all policy '%s'", ssid_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("Policy '%s' already exists — continuing", ssid_name)
            else:
                result["errors"].append(f"create_policy: {exc}")
                logger.error("Failed to create policy '%s': %s", ssid_name, exc)

        try:
            central_client._request(
                "PATCH",
                "/network-config/v1alpha1/policy-groups",
                json={"policy-group": {"policy-group-list": [{"name": ssid_name, "position": 3}]}},
            )
            logger.info("Added '%s' to policy group", ssid_name)
        except Exception as exc:
            result["errors"].append(f"add_policy_group: {exc}")
            logger.error("Failed to add '%s' to policy group: %s", ssid_name, exc)

        global_scope_id_pol = _fetch_global_scope_id(central_client)
        for persona in ("CAMPUS_AP", "MOBILITY_GW"):
            pol_scope_map = {
                "scope-map": [{
                    "scope-name": global_scope_id_pol,
                    "scope-id": int(global_scope_id_pol),
                    "persona": persona,
                    "resource": f"policies/{ssid_name}",
                }]
            }
            try:
                central_client.post("/network-config/v1/scope-maps", data=pol_scope_map)
                logger.info("Scope-mapped policies/%s → %s global", ssid_name, persona)
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
                if "already exists" in resp_text.lower():
                    logger.warning("Scope-map for policies/%s (%s) already exists", ssid_name, persona)
                else:
                    result["errors"].append(f"scope_map_policy ({persona}): {exc}")
                    logger.error("Failed to scope-map policy '%s' (%s): %s", ssid_name, persona, exc)

    # ------------------------------------------------------------------
    # Step 2: Create wlan-ssid
    # ------------------------------------------------------------------
    endpoint = f"/network-config/v1/wlan-ssids/{url_name}"
    if dry_run:
        logger.info("[dry-run] Would POST %s (overlay, FORWARD_MODE_L2)", endpoint)
        result["created"] = True
    else:
        try:
            central_client.post(endpoint, data=body)
            result["created"] = True
            logger.info("Created overlay SSID '%s'", ssid_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("SSID '%s' already exists — continuing", ssid_name)
                result["created"] = True
            else:
                result["errors"].append(f"create_ssid: {exc}")
                logger.error("Failed to create overlay SSID '%s': %s", ssid_name, exc)
                return result

    # The API silently drops default-role on POST — patch it in after creation
    if not dry_run and result["created"]:
        try:
            central_client.patch(endpoint, data={"default-role": ssid_name})
            logger.info("Patched default-role='%s' on SSID '%s'", ssid_name, ssid_name)
        except Exception as exc:
            result["errors"].append(f"patch_default_role: {exc}")
            logger.error("Failed to patch default-role on SSID '%s': %s", ssid_name, exc)

    # ------------------------------------------------------------------
    # Step 4: Create overlay-wlan profile
    # ------------------------------------------------------------------
    overlay_body = {
        "profile": ssid_name,
        "overlay-profile-type": "WIRELESS_PROFILE",
        "essid-name": ssid_name,
        "gw-cluster-list": [
            {
                "cluster-redundancy-type": "PRIMARY",
                "cluster": cluster_name,
                "cluster-scope-id": cluster_scope_id,
                "cluster-type": "CLUSTER_ID",
                "tunnel-type": "GRE",
            }
        ],
    }
    overlay_endpoint = f"/network-config/v1/overlay-wlan/{url_name}"
    if dry_run:
        logger.info("[dry-run] Would POST %s with cluster=%s", overlay_endpoint, cluster_name)
        result["overlay_created"] = True
    else:
        try:
            central_client.post(overlay_endpoint, data=overlay_body)
            result["overlay_created"] = True
            logger.info("Created overlay-wlan profile '%s'", ssid_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
            if "duplicate" in resp_text.lower() or "already exists" in resp_text.lower():
                logger.warning("overlay-wlan '%s' already exists — continuing", ssid_name)
                result["overlay_created"] = True
            else:
                result["errors"].append(f"create_overlay_wlan: {exc}")
                logger.error("Failed to create overlay-wlan '%s': %s", ssid_name, exc)
                return result

    # ------------------------------------------------------------------
    # Steps 3-4: Scope-map wlan-ssid and overlay-wlan (role already mapped above)
    # ------------------------------------------------------------------
    scope_maps = [
        ("CAMPUS_AP", f"wlan-ssids/{ssid_name}"),
        ("CAMPUS_AP", f"overlay-wlan/{ssid_name}"),
    ]

    all_mapped = True
    for persona, resource in scope_maps:
        scope_map_body = {
            "scope-map": [
                {
                    "scope-name": scope_id,
                    "scope-id": int(scope_id),
                    "persona": persona,
                    "resource": resource,
                }
            ]
        }
        if dry_run:
            logger.info("[dry-run] Would POST scope-maps — %s scope=%s resource=%s", persona, scope_id, resource)
        else:
            try:
                central_client.post("/network-config/v1/scope-maps", data=scope_map_body)
                logger.info("Scope-mapped %s → %s scope-id=%s", resource, persona, scope_id)
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or str(exc)
                if "already exists" in resp_text.lower():
                    logger.warning("Scope-map for '%s' already exists — skipping", resource)
                else:
                    result["errors"].append(f"scope_map ({resource}): {exc}")
                    logger.error("Failed to scope-map %s: %s", resource, exc)
                    all_mapped = False

    result["scope_mapped"] = all_mapped
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
