"""Stage 6 — Configure: site creation, hostname, persona, and group assignment.

Uses direct New Central REST calls per the Configuration APIs runbook (v2026.0331).
No vendor SDK dependency in this stage.
"""

from __future__ import annotations

import logging
from typing import Any

from pipeline.models import AccountContext, DeviceRecord, StageResult
from pipeline.state_store import StateStore
from pipeline.stages.base import Stage

logger = logging.getLogger(__name__)


ARUBA_DEVICE_PROFILES: list[dict] = [
    {
        "name": "arubaAP",
        "role": "arubaAP",
        "lldp-group-entries": [
            {
                "sequence-number": 1,
                "action": "MATCH",
                "vendor-oui": "000B86",
                "vendor-oui-subtype": [{"type": 1, "value": "0001"}],
            },
        ],
    },
    {
        "name": "arubaGW",
        "role": "arubaGW",
        "lldp-group-entries": [
            {
                "sequence-number": 1,
                "action": "MATCH",
                "vendor-oui": "000B86",
                "vendor-oui-subtype": [{"type": 1, "value": "0002"}],
            },
        ],
    },
    {
        "name": "arubaSW",
        "role": "arubaSW",
        "lldp-group-entries": [
            {
                "sequence-number": 1,
                "action": "MATCH",
                "vendor-oui": "883A30",
                "vendor-oui-subtype": [{"type": 2, "value": "0001"}],
            },
            {
                "sequence-number": 2,
                "action": "MATCH",
                "vendor-oui": "0016B9",
                "vendor-oui-subtype": [{"type": 2, "value": "0001"}],
            },
        ],
    },
    {
        "name": "arubaAOS",
        "role": "arubaAOS",
        "lldp-group-entries": [
            {
                "sequence-number": 1,
                "action": "MATCH",
                "vendor-oui": "0016B9",
                "vendor-oui-subtype": [{"type": 2, "value": "0001"}],
            },
        ],
    },
]


def _fetch_global_scope_id(central_client: Any) -> str:
    """Discover the account-root global scope-id.

    Reads existing scope-maps and returns the scope-id that appears on
    SERVICE_PERSONA entries — these are always at the org/global level.
    Falls back to the site's parent scope-id if no SERVICE_PERSONA entries exist.
    """
    result = central_client.get("/network-config/v1/scope-maps")
    scope_maps = result.get("scope-map", [])

    # SERVICE_PERSONA entries are always scoped to the org/global level
    for entry in scope_maps:
        if entry.get("persona") == "SERVICE_PERSONA":
            return str(entry["scope-id"])

    # Fallback: collect all scope-ids that are NOT device-level (device scopes
    # appear paired with a non-SERVICE_PERSONA persona like ACCESS_SWITCH).
    # The global scope-id is the one that appears most frequently.
    from collections import Counter
    counts: Counter = Counter(str(e["scope-id"]) for e in scope_maps)
    if counts:
        return counts.most_common(1)[0][0]

    raise RuntimeError("Could not determine global scope-id from scope-maps")


def _post_scope_map(
    central_client: Any,
    scope_id: str,
    persona: str,
    resource: str,
) -> None:
    """POST /network-config/v1/scope-maps for a single scope/persona/resource triple.

    Raises on HTTP error. Caller wraps in try/except.
    """
    central_client.post(
        "/network-config/v1/scope-maps",
        data={
            "scope-map": [
                {
                    "scope-name": scope_id,
                    "scope-id": int(scope_id),
                    "persona": persona,
                    "resource": resource,
                }
            ]
        },
    )


def _ensure_device_profiles(central_client: Any, target_ctx: Any) -> None:
    """Create the four standard Aruba LLDP device profiles at the library level.

    Step 1: Create switch_allow_all policy + add to policy group + scope-map at global.
    Step 2: Create port profiles (roles) referencing switch_allow_all; scope-map at global
            and Switches group.
    Step 3: Create device profiles with LLDP match rules via v1alpha1.

    Silently skips anything that already exists. Idempotent — safe to re-run.
    Sets target_ctx.device_profiles_created = True so subsequent devices skip this step.
    """
    if target_ctx.device_profiles_created:
        return

    _GLOBAL_SCOPE_ID = "79236221864456192"
    _SWITCHES_SCOPE_ID = "79244358948933632"
    _PROFILE_NAMES = [p["name"] for p in ARUBA_DEVICE_PROFILES]

    def _skip(response_text: str) -> bool:
        t = response_text.lower()
        return "duplicate" in t or "already exists" in t

    # Step 1: Port profiles (roles) — no policy needed for CX switch device identity roles
    _ROLE_BODIES: dict[str, dict] = {
        "arubaAP": {
            "name": "arubaAP",
            "description": "arubaAP",
            "vlan-parameters": {"wired-access-vlan-id": 5},
            "session-parameters": {"auth-mode": "DEVICE_MODE", "stp-admin-edge-port": True, "poe-priority": "CRITICAL", "tunneled-node-server-redirect": False},
        },
    }
    for name in _PROFILE_NAMES:
        role_body = _ROLE_BODIES.get(name, {"name": name, "description": name})
        try:
            central_client.post(f"/network-config/v1/roles/{name}", data=role_body)
            logger.debug("Created port profile (role) '%s'", name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
            if _skip(resp_text):
                # Update existing role to ensure vlan-parameters/session-parameters are applied
                try:
                    central_client.put(f"/network-config/v1/roles/{name}", data=role_body)
                    logger.debug("Updated existing port profile (role) '%s'", name)
                except Exception as exc2:
                    logger.warning("Role '%s' update failed: %s — continuing", name, exc2)
            else:
                logger.warning("Port profile '%s' creation failed: %s — continuing", name, exc)

        # Scope-map role at global scope and Switches group
        for scope_id in (_GLOBAL_SCOPE_ID, _SWITCHES_SCOPE_ID):
            try:
                _post_scope_map(central_client, scope_id, "ACCESS_SWITCH", f"roles/{name}")
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
                if not _skip(resp_text):
                    logger.warning("Role '%s' scope-map (scope=%s) failed: %s — continuing", name, scope_id, exc)

    # Step 2: Port profiles (sw-port-profiles)
    _PORT_PROFILES = [
        {
            "name": "aruba-ap-access",
            "description": "Aruba AP uplink — overlay mode, access VLAN 5",
            "body": {
                "mode": "AUTO",
                "enable": True,
                "routing": False,
                "dpi-enable": True,
                "lldp": {"mode": "TX_RX"},
                "switchport": {"interface-mode": "ACCESS", "access-vlan": 5},
                "stp": {
                    "enable": True,
                    "admin-edge-port": True,
                    "bpdu-guard": True,
                    "bpdu-filter": False,
                    "loop-guard": False,
                    "root-guard": False,
                    "rpvst-filter": False,
                    "rpvst-guard": False,
                    "tcn-guard": False,
                    "priority": 6,
                },
                "poe": {"enabled": True, "allocation-method": "USAGE", "priority": "CRITICAL"},
            },
        },
        {
            "name": "aruba-sw-trunk",
            "description": "Switch-to-switch trunk — all VLANs, no edge/guard, jumbo MTU",
            "body": {
                "mode": "AUTO",
                "enable": True,
                "routing": False,
                "dpi-enable": False,
                "mtu": 9198,
                "lldp": {"mode": "TX_RX"},
                "switchport": {
                    "interface-mode": "TRUNK",
                    "native-vlan": 1,
                },
                "stp": {
                    "enable": True,
                    "admin-edge-port": False,
                    "bpdu-guard": False,
                    "bpdu-filter": False,
                    "loop-guard": False,
                    "root-guard": False,
                    "rpvst-filter": False,
                    "rpvst-guard": False,
                    "tcn-guard": False,
                    "priority": 6,
                },
                "poe": {"enabled": False},
            },
        },
    ]
    for pp in _PORT_PROFILES:
        pp_name = pp["name"]
        try:
            central_client.post(f"/network-config/v1/sw-port-profiles/{pp_name}", data={"description": pp["description"]})
            central_client.put(f"/network-config/v1/sw-port-profiles/{pp_name}", data=pp["body"])
            logger.debug("Created port profile '%s'", pp_name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
            if not _skip(resp_text):
                logger.warning("Port profile '%s' creation failed: %s — continuing", pp_name, exc)

        for scope_id in (_GLOBAL_SCOPE_ID, _SWITCHES_SCOPE_ID):
            try:
                _post_scope_map(central_client, scope_id, "ACCESS_SWITCH", f"sw-port-profiles/{pp_name}")
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
                if not _skip(resp_text):
                    logger.warning("Port profile '%s' scope-map (scope=%s) failed: %s — continuing", pp_name, scope_id, exc)

    # Step 3: Device profiles with LLDP match rules + scope-maps
    for profile in ARUBA_DEVICE_PROFILES:
        name = profile["name"]
        try:
            central_client.post(f"/network-config/v1alpha1/device-profile/{name}", data=profile)
            logger.debug("Created device profile '%s'", name)
        except Exception as exc:
            resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
            if not _skip(resp_text):
                logger.warning("Device profile '%s' creation failed: %s — continuing", name, exc)

        for scope_id in (_GLOBAL_SCOPE_ID, _SWITCHES_SCOPE_ID):
            try:
                _post_scope_map(central_client, scope_id, "ACCESS_SWITCH", f"device-profile/{name}")
            except Exception as exc:
                resp_text = getattr(getattr(exc, "response", None), "text", "") or ""
                if not _skip(resp_text):
                    logger.warning("Device profile '%s' scope-map (scope=%s) failed: %s — continuing", name, scope_id, exc)

    target_ctx.device_profiles_created = True


def _push_vlan_interface(
    central_client: Any,
    vi: dict,
    device_scope_id: str,
    global_scope_id: str,
    persona: str,
) -> None:
    """Push a single VLAN L3 interface.

    Args:
        vi: Dict with keys: vlan (int), ip_address (str|None),
            helper_address (str|None), dhcp (bool).
        device_scope_id: Numeric scope-id string for this device.
        global_scope_id: Org-level scope-id string.
        persona: e.g. "ACCESS_SWITCH".
    """
    vlan_id = vi["vlan"]

    # Step 1: Upsert L2 VLAN
    l2_body = {"vlan": vlan_id, "name": str(vlan_id), "enable": True}
    try:
        central_client.post(f"/network-config/v1/layer2-vlan/{vlan_id}", data=l2_body)
    except Exception as exc:
        response_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "duplicate" not in response_text.lower():
            central_client.put(f"/network-config/v1/layer2-vlan/{vlan_id}", data=l2_body)

    # Step 2: Create vlan-interface globally (no IP — just the L3 shell)
    global_body: dict = {"id": vlan_id, "is-valid": True, "enable": True}
    try:
        central_client.post(f"/network-config/v1/vlan-interfaces/{vlan_id}", data=global_body)
    except Exception as exc:
        response_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "duplicate" not in response_text.lower():
            central_client.put(f"/network-config/v1/vlan-interfaces/{vlan_id}", data=global_body)

    # Step 3: Override IP at device local scope
    if vi["ip_address"] and not vi["dhcp"]:
        local_body: dict = {"id": vlan_id, "is-valid": True, "enable": True, "ipv4": {"address": vi["ip_address"]}}
        if vi["helper_address"]:
            local_body["ipv4-relay"] = {"server": [{"ip": vi["helper_address"], "vrf": "default", "ip-vrf": f"{vi['helper_address']}~default"}]}
        local_params = {"scope-id": device_scope_id, "view-type": "LOCAL"}
        try:
            central_client.post(f"/network-config/v1/vlan-interfaces/{vlan_id}", params=local_params, data=local_body)
        except Exception:
            central_client.put(f"/network-config/v1/vlan-interfaces/{vlan_id}", params=local_params, data=local_body)

    # Step 4: Scope-maps
    try:
        _post_scope_map(central_client, global_scope_id, persona, f"layer2-vlan/{vlan_id}")
    except Exception as exc:
        response_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "already exists" not in response_text.lower():
            raise
    try:
        _post_scope_map(central_client, device_scope_id, persona, f"vlan-interfaces/{vlan_id}")
    except Exception as exc:
        response_text = getattr(getattr(exc, "response", None), "text", "") or ""
        if "already exists" not in response_text.lower():
            raise

    logger.debug(
        "Pushed VLAN interface %d (%s) for scope-id=%s",
        vlan_id,
        "dhcp" if vi["dhcp"] else vi["ip_address"],
        device_scope_id,
    )


class ConfigureStage(Stage):
    name = "s6_configure"

    def _execute(
        self,
        record: DeviceRecord,
        run_id: str,
        source_ctx: AccountContext,
        target_ctx: AccountContext,
        state: StateStore,
        dry_run: bool,
    ) -> StageResult:
        if dry_run:
            return StageResult.skipped("dry-run — skipping write operations")

        central = target_ctx.central_client
        mcp = target_ctx.mcp_client
        errors: list[str] = []
        site_id = record.site_id

        # 0. Ensure global scope-id is cached on AccountContext (shared across all devices)
        if not target_ctx.global_scope_id:
            try:
                target_ctx.global_scope_id = _fetch_global_scope_id(central)
                logger.info("Resolved global scope-id=%s", target_ctx.global_scope_id)
            except Exception as exc:
                return StageResult.failed(f"CONFIGURE_FAILED: global scope-id discovery — {exc}")

        # 0.5. Ensure Aruba device profiles exist at library level (once per run)
        _ensure_device_profiles(central, target_ctx)

        # 1. Resolve device scope-id (populated in S5; re-fetch if missing)
        device_scope_id = record.scope_id
        if not device_scope_id:
            device_scope_id = mcp.get_device_scope_id(record.serial_number)
            if not device_scope_id:
                return StageResult.failed(
                    f"CONFIGURE_FAILED: could not resolve device scope-id for {record.serial_number} "
                    "via /network-config/v1alpha1/devices"
                )
            record.scope_id = device_scope_id

        # 2. Site creation (if needed)
        if record.needs_site_create:
            logger.info("Creating site '%s' for %s", record.target_site, record.serial_number)
            try:
                central.post(
                    "/network-monitoring/v1/sites",
                    data={"name": record.target_site},
                )
                # Re-fetch site list to get the new site_id
                new_site = mcp.get_site_by_name(record.target_site)
                if new_site:
                    site_id = new_site.get("id") or new_site.get("scopeId") or new_site.get("siteId")
                    record.site_id = site_id
                    logger.info("Created site '%s' → id=%s", record.target_site, site_id)
                else:
                    errors.append("site_create: site not found after creation")
            except Exception as exc:
                errors.append(f"site_create: {exc}")

        if errors:
            return StageResult.failed(f"CONFIGURE_FAILED: {'; '.join(errors)}", site_id=site_id)

        # 3. Set hostname alias at device scope (soft failure — non-blocking)
        try:
            central.post(
                "/network-config/v1/aliases/sys_host_name",
                params={"view-type": "LOCAL", "scope-id": device_scope_id},
                data={
                    "name": "sys_host_name",
                    "type": "ALIAS_HOSTNAME",
                    "default-value": {
                        "hostname-value": {"hostname": record.serial_number}
                    },
                },
            )
            logger.debug("Set hostname alias for %s", record.serial_number)
        except Exception as exc:
            logger.warning("Hostname alias failed for %s: %s — continuing", record.serial_number, exc)

        # 4. Persona assignment via scope-map at device scope (soft failure — device may already be assigned)
        persona_api = record.persona.to_api_value()  # "ACCESS_SWITCH", "CORE_SWITCH", "AGG_SWITCH"
        logger.info("Setting persona '%s' on %s (scope-id=%s)", persona_api, record.serial_number, device_scope_id)
        try:
            _post_scope_map(central, device_scope_id, persona_api, f"persona/{persona_api}")
        except Exception as exc:
            logger.warning("Persona assignment failed for %s: %s — continuing (may already be set)", record.serial_number, exc)

        # 5. Group assignment via configuration/v1/devices/move (direct REST)
        logger.info("Assigning %s to group '%s'", record.serial_number, record.target_group)
        try:
            central.post(
                "/configuration/v1/devices/move",
                data={"group": record.target_group, "serials": [record.serial_number]},
            )
        except Exception as exc:
            logger.warning(
                "Group assignment failed for %s: %s — continuing (device may already be in group)",
                record.serial_number,
                exc,
            )

        # 6. Push VLANs from AOS 8 config file (ACCESS_SWITCH persona only)
        vlans_pushed = 0
        if record.vlan_config_file and record.persona.to_api_value() == "ACCESS_SWITCH":
            from pipeline.vlan_loader import load_vlan_config_file
            try:
                vlans = load_vlan_config_file(record.vlan_config_file)
            except Exception as exc:
                logger.warning("VLAN config load failed for %s: %s — skipping", record.serial_number, exc)
                vlans = []
            if vlans:
                try:
                    central.post("/network-config/v1/layer2-vlan", data={"l2-vlan": vlans})
                    vlans_pushed = len(vlans)
                except Exception as exc:
                    logger.warning("VLAN push failed for %s: %s — continuing", record.serial_number, exc)
                # Scope-map each VLAN independently — one duplicate/failure
                # (e.g. on a resumed run) must not block the rest of the batch.
                for vlan in vlans:
                    try:
                        _post_scope_map(
                            central,
                            target_ctx.global_scope_id,
                            "ACCESS_SWITCH",
                            f"layer2-vlan/{vlan['vlan']}",
                        )
                    except Exception as exc:
                        logger.warning(
                            "VLAN %s scope-map failed for %s: %s — continuing",
                            vlan.get("vlan"), record.serial_number, exc,
                        )
                if vlans_pushed:
                    logger.info(
                        "Pushed %d VLANs for %s (scope-id=%s)",
                        vlans_pushed, record.serial_number, target_ctx.global_scope_id,
                    )

        # 7. Push VLAN interfaces (L3) from config file
        vlan_interfaces_pushed = 0
        if record.vlan_interface_config_file:
            from pipeline.vlan_interface_loader import load_vlan_interface_config_file
            try:
                vlan_intfs = load_vlan_interface_config_file(record.vlan_interface_config_file)
            except Exception as exc:
                logger.warning(
                    "VLAN interface config load failed for %s: %s — skipping",
                    record.serial_number, exc,
                )
                vlan_intfs = []
            # Push each interface independently — one failure (e.g. a
            # duplicate on a resumed run) must not block the rest of the batch.
            for vi in vlan_intfs:
                try:
                    _push_vlan_interface(
                        central, vi, device_scope_id, target_ctx.global_scope_id, persona_api
                    )
                    vlan_interfaces_pushed += 1
                except Exception as exc:
                    logger.warning(
                        "VLAN interface push failed for %s vlan=%s: %s — continuing",
                        record.serial_number, vi.get("vlan"), exc,
                    )
            if vlan_intfs:
                logger.info(
                    "Pushed %d/%d VLAN interface(s) for %s",
                    vlan_interfaces_pushed, len(vlan_intfs), record.serial_number,
                )

        return StageResult.success(
            site_id=site_id,
            persona=persona_api,
            scope_id=device_scope_id,
            global_scope_id=target_ctx.global_scope_id,
            vlans_pushed=vlans_pushed,
            vlan_interfaces_pushed=vlan_interfaces_pushed,
        )
