"""Deterministic AOS8 -> Classic Central / New Central migration planning.

Pure-python transform: no network calls, no target-account writes, and no
import of `mcp_servers/` (keeps this testable without any MCP/FastMCP
scaffolding and safe to run in a plain unit test). Consumes an
`aos8_export_all()`-shaped dict and returns a deterministic, JSON-serializable
plan with:

- ``candidates.classic_central`` / ``candidates.new_central`` — explicit,
  typed target payload proposals (never a raw guess).
- ``warnings`` — every known-lossy or unsupported field translation, sorted
  and de-duplicated, so nothing is silently dropped.
- ``diff`` — a stable (sorted-key) before/after comparison per object, one
  entry per candidate.
- ``verification_plan`` — read-only post-migration checks, referenced by
  *tool name string only*. This module intentionally never calls another MCP
  server's tool directly: verification is left to the caller (human or
  router) so this stays a pure, ownership-boundary-respecting transform.
"""

from __future__ import annotations

from typing import Any

from pipeline.aos8_parsers import parse_export
from pipeline.aos8_schema import (
    AOS8ApGroup,
    AOS8Controller,
    AOS8Policy,
    AOS8Role,
    AOS8Vlan,
    AOS8Wlan,
    ClassicCentralCandidate,
    NewCentralCandidate,
    UNSUPPORTED_FIELDS,
)


def _sorted_items(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(payload.items(), key=lambda kv: kv[0])


def _diff_entry(source: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _sorted_items(source),
        "candidate": _sorted_items(candidate),
    }


def _wlan_classic_payload(wlan: AOS8Wlan) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    payload = {
        "name": wlan.profile_name,
        "essid": wlan.essid or wlan.profile_name,
        "vlan": wlan.vlan,
    }
    if wlan.opmode:
        warnings.append(f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['opmode']}")
    if wlan.forward_mode:
        warnings.append(
            f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['forward_mode']}"
        )
    return payload, warnings


def _wlan_new_central_payload(wlan: AOS8Wlan) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    payload = {
        "essid": wlan.essid or wlan.profile_name,
        "vlan": wlan.vlan,
        "aaa_profile": wlan.aaa_profile,
    }
    if wlan.opmode:
        warnings.append(f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['opmode']}")
    if wlan.forward_mode:
        warnings.append(
            f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['forward_mode']}"
        )
    return payload, warnings


def _role_classic_payload(role: AOS8Role) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    payload = {"name": role.rolename, "vlan": role.vlan, "acl": role.acl}
    if role.captive_portal_profile:
        warnings.append(
            f"role:{role.rolename}: {UNSUPPORTED_FIELDS['role']['captive_portal_profile']}"
        )
    return payload, warnings


def _role_new_central_payload(role: AOS8Role) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    payload = {"name": role.rolename, "vlan": role.vlan}
    if role.captive_portal_profile:
        warnings.append(
            f"role:{role.rolename}: {UNSUPPORTED_FIELDS['role']['captive_portal_profile']}"
        )
    return payload, warnings


def _vlan_payload(vlan: AOS8Vlan) -> dict[str, Any]:
    return {"vlan_id": vlan.vlan_id, "description": vlan.description}


def _ap_group_payload(group: AOS8ApGroup) -> dict[str, Any]:
    return {"name": group.profile_name, "wlan_profiles": sorted(group.virtual_ap_profiles)}


def _controller_payload(controller: AOS8Controller) -> dict[str, Any]:
    return {
        "name": controller.name,
        "ip_address": controller.ip_address,
        "model": controller.model,
    }


def _policy_payload(policy: AOS8Policy) -> tuple[dict[str, Any], list[str]]:
    warnings = [f"policy:{policy.name}: {UNSUPPORTED_FIELDS['policy']['rule_count']}"]
    return {"name": policy.name, "rule_count": policy.rule_count}, warnings


def _default_verification_plan(config_path: str) -> list[dict[str, Any]]:
    """Read-only checks named by tool string; this module never invokes them.

    Tool names match existing `verb_noun` tools already registered in
    `mcp_servers/config.py` and `mcp_servers/monitoring.py` (no product
    prefix, per repo convention) so a caller can dispatch them by name via
    the router without this module importing those servers directly.
    """
    return [
        {
            "tool": "list_overlay_wlans",
            "args": {},
            "purpose": "Confirm migrated WLAN/SSID names and VLANs match the AOS8 export.",
        },
        {
            "tool": "list_roles",
            "args": {},
            "purpose": "Confirm migrated user roles and VLAN assignments match the AOS8 export.",
        },
        {
            "tool": "list_named_vlans",
            "args": {},
            "purpose": "Confirm every AOS8 VLAN ID exists on the target account.",
        },
        {
            "tool": "list_devices",
            "args": {},
            "purpose": (
                f"Confirm AP/controller inventory previously under AOS8 "
                f"config_path {config_path!r} appears healthy on the target account."
            ),
        },
    ]


def build_migration_plan(export: dict[str, Any]) -> dict[str, Any]:
    """Turn an `aos8_export_all()`-shaped export into a deterministic migration plan."""
    parsed = parse_export(export)
    config_path = export.get("config_path", "/md") if isinstance(export, dict) else "/md"

    classic_candidates: list[dict[str, Any]] = []
    new_candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not isinstance(export, dict):
        warnings.append("export: expected an object; no source objects were parsed.")
    else:
        source_warnings = export.get("warnings", [])
        if isinstance(source_warnings, list):
            warnings.extend(f"export: {warning}" for warning in source_warnings if warning)
        elif source_warnings:
            warnings.append("export: warnings field was malformed and could not be parsed.")

        wlans = export.get("wlans")
        if not isinstance(wlans, dict):
            warnings.append("export: wlans section is missing or malformed.")
        else:
            for section in ("ssid_profiles", "virtual_aps"):
                value = wlans.get(section)
                if not isinstance(value, list):
                    warnings.append(f"export: wlans.{section} is missing or malformed.")
                else:
                    dropped = sum(not isinstance(item, dict) for item in value)
                    if dropped:
                        warnings.append(
                            f"export: wlans.{section} dropped {dropped} malformed item(s)."
                        )

        for section in ("roles", "vlans", "ap_groups", "controllers", "policies"):
            value = export.get(section)
            if not isinstance(value, list):
                warnings.append(f"export: {section} section is missing or malformed.")
                continue
            dropped = sum(not isinstance(item, dict) for item in value)
            if dropped:
                warnings.append(
                    f"export: {section} dropped {dropped} malformed item(s)."
                )
    diff: dict[str, Any] = {}

    for wlan in parsed["wlans"]:
        classic_payload, classic_warnings = _wlan_classic_payload(wlan)
        new_payload, new_warnings = _wlan_new_central_payload(wlan)
        classic_candidates.append(
            ClassicCentralCandidate(
                "wlan", wlan.profile_name, classic_payload, sorted(classic_warnings)
            ).to_dict()
        )
        new_candidates.append(
            NewCentralCandidate(
                "wlan", wlan.profile_name, new_payload, sorted(new_warnings)
            ).to_dict()
        )
        warnings.extend(classic_warnings)
        warnings.extend(new_warnings)
        diff[f"wlan:{wlan.profile_name}"] = _diff_entry(wlan.to_dict(), new_payload)

    for role in parsed["roles"]:
        classic_payload, classic_warnings = _role_classic_payload(role)
        new_payload, new_warnings = _role_new_central_payload(role)
        classic_candidates.append(
            ClassicCentralCandidate(
                "role", role.rolename, classic_payload, sorted(classic_warnings)
            ).to_dict()
        )
        new_candidates.append(
            NewCentralCandidate(
                "role", role.rolename, new_payload, sorted(new_warnings)
            ).to_dict()
        )
        warnings.extend(classic_warnings)
        warnings.extend(new_warnings)
        diff[f"role:{role.rolename}"] = _diff_entry(role.to_dict(), new_payload)

    for vlan in parsed["vlans"]:
        payload = _vlan_payload(vlan)
        identifier = str(vlan.vlan_id)
        classic_candidates.append(
            ClassicCentralCandidate("vlan", identifier, payload).to_dict()
        )
        new_candidates.append(NewCentralCandidate("vlan", identifier, payload).to_dict())
        diff[f"vlan:{identifier}"] = _diff_entry(vlan.to_dict(), payload)

    for group in parsed["ap_groups"]:
        payload = _ap_group_payload(group)
        classic_candidates.append(
            ClassicCentralCandidate("ap_group", group.profile_name, payload).to_dict()
        )
        new_candidates.append(
            NewCentralCandidate("ap_group", group.profile_name, payload).to_dict()
        )
        diff[f"ap_group:{group.profile_name}"] = _diff_entry(group.to_dict(), payload)

    for controller in parsed["controllers"]:
        payload = _controller_payload(controller)
        identifier = controller.name or controller.ip_address or "unknown"
        classic_candidates.append(
            ClassicCentralCandidate("controller", identifier, payload).to_dict()
        )
        # Controllers/Mobility Conductors have no New Central equivalent object;
        # gateways/APs are onboarded individually, so no new_central candidate.
        warnings.append(
            f"controller:{identifier}: AOS8 controllers/Mobility Conductors are not "
            "migrated as objects; onboard replacement gateways/APs individually."
        )
        diff[f"controller:{identifier}"] = _diff_entry(controller.to_dict(), payload)

    for policy in parsed["policies"]:
        payload, policy_warnings = _policy_payload(policy)
        classic_candidates.append(
            ClassicCentralCandidate("policy", policy.name, payload, sorted(policy_warnings)).to_dict()
        )
        new_candidates.append(
            NewCentralCandidate("policy", policy.name, payload, sorted(policy_warnings)).to_dict()
        )
        warnings.extend(policy_warnings)
        diff[f"policy:{policy.name}"] = _diff_entry(policy.to_dict(), payload)

    return {
        "config_path": config_path,
        "candidates": {
            "classic_central": classic_candidates,
            "new_central": new_candidates,
        },
        "warnings": sorted(set(warnings)),
        "diff": dict(sorted(diff.items())),
        "verification_plan": _default_verification_plan(config_path),
        "source_object_counts": {key: len(value) for key, value in sorted(parsed.items())},
    }
