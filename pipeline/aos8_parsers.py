"""Parsers turning `aos8_export_all()`-shaped export dicts into normalized objects.

Pure-python, no network calls. `mcp_servers/aos8.py` produces the export shape
consumed here (see `aos8_export_all`); keeping the parser in `pipeline/` keeps
it independently unit-testable with canned dicts and reusable outside the MCP
tool layer.

Expected export shape (extra/missing keys are tolerated):

    {
        "config_path": "/md",
        "wlans": {"ssid_profiles": [...], "virtual_aps": [...]},
        "roles": [...],
        "vlans": [...],
        "ap_groups": [...],
        "controllers": [...],
        "policies": [...],
        "warnings": [...],
    }

Raw AOS8 config-object field names are inconsistent between read and write
paths (for example a `role` object's GET response uses `role` as the display
key while the POST/write identifier field is `rolename`), so every parser
below probes a short list of known-good candidate keys rather than assuming
one canonical name.
"""

from __future__ import annotations

from typing import Any

from pipeline.aos8_schema import (
    AOS8ApGroup,
    AOS8Controller,
    AOS8Policy,
    AOS8Role,
    AOS8Vlan,
    AOS8Wlan,
)


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_wlans(export: dict[str, Any]) -> list[AOS8Wlan]:
    """Merge SSID profiles with their linked virtual AP (matched by name)."""
    wlans_section = export.get("wlans")
    if not isinstance(wlans_section, dict):
        return []
    ssid_profiles = _as_dict_list(wlans_section.get("ssid_profiles"))
    virtual_aps = _as_dict_list(wlans_section.get("virtual_aps"))

    vap_by_ssid: dict[str, dict[str, Any]] = {}
    for vap in virtual_aps:
        ssid_ref = _first(vap, ("ssid-profile", "ssid_prof"))
        if ssid_ref:
            vap_by_ssid[str(ssid_ref)] = vap

    out: list[AOS8Wlan] = []
    for profile in ssid_profiles:
        name = _first(profile, ("profile-name", "name"))
        if not name:
            continue
        vap = vap_by_ssid.get(str(name), {})
        out.append(
            AOS8Wlan(
                profile_name=str(name),
                essid=_first(profile, ("essid", "ESSID")),
                opmode=_first(profile, ("opmode",)),
                vlan=_first(vap, ("vlan",)),
                forward_mode=_first(vap, ("forward-mode", "forward_mode")),
                aaa_profile=_first(vap, ("aaa-profile", "aaa_prof")),
                virtual_ap_profile=_first(vap, ("profile-name", "name")),
                raw={"ssid_profile": profile, "virtual_ap": vap},
            )
        )

    # Virtual APs with no matching SSID profile still count as WLANs.
    referenced = {wlan.virtual_ap_profile for wlan in out if wlan.virtual_ap_profile}
    for vap in virtual_aps:
        vap_name = _first(vap, ("profile-name", "name"))
        if not vap_name or vap_name in referenced:
            continue
        out.append(
            AOS8Wlan(
                profile_name=str(vap_name),
                vlan=_first(vap, ("vlan",)),
                forward_mode=_first(vap, ("forward-mode", "forward_mode")),
                aaa_profile=_first(vap, ("aaa-profile", "aaa_prof")),
                virtual_ap_profile=str(vap_name),
                raw={"ssid_profile": {}, "virtual_ap": vap},
            )
        )
    return out


def parse_roles(export: dict[str, Any]) -> list[AOS8Role]:
    items = _as_dict_list(export.get("roles"))
    out: list[AOS8Role] = []
    for item in items:
        name = _first(item, ("rolename", "role", "name", "profile-name"))
        if not name:
            continue
        out.append(
            AOS8Role(
                rolename=str(name),
                vlan=_first(item, ("vlan",)),
                acl=_first(item, ("acl", "access-list")),
                captive_portal_profile=_first(item, ("captive-portal-profile",)),
                raw=item,
            )
        )
    return out


def parse_vlans(export: dict[str, Any]) -> list[AOS8Vlan]:
    items = _as_dict_list(export.get("vlans"))
    out: list[AOS8Vlan] = []
    for item in items:
        vlan_id = _first(item, ("id", "vlan-id", "vlan_id", "name"))
        if vlan_id is None:
            continue
        out.append(
            AOS8Vlan(
                vlan_id=vlan_id,
                description=_first(item, ("description",)),
                raw=item,
            )
        )
    return out


def parse_ap_groups(export: dict[str, Any]) -> list[AOS8ApGroup]:
    items = _as_dict_list(export.get("ap_groups"))
    out: list[AOS8ApGroup] = []
    for item in items:
        name = _first(item, ("profile-name", "name"))
        if not name:
            continue
        vaps = item.get("virtual-ap") or item.get("virtual_ap") or []
        vap_names = [str(v) for v in vaps] if isinstance(vaps, list) else []
        out.append(AOS8ApGroup(profile_name=str(name), virtual_ap_profiles=vap_names, raw=item))
    return out


def parse_controllers(export: dict[str, Any]) -> list[AOS8Controller]:
    items = _as_dict_list(export.get("controllers"))
    out: list[AOS8Controller] = []
    for item in items:
        out.append(
            AOS8Controller(
                name=_first(item, ("Name", "name", "hostname")),
                ip_address=_first(item, ("IP Address", "ip_address")),
                model=_first(item, ("Model", "model")),
                version=_first(item, ("Version", "version")),
                raw=item,
            )
        )
    return out


def parse_policies(export: dict[str, Any]) -> list[AOS8Policy]:
    items = _as_dict_list(export.get("policies"))
    out: list[AOS8Policy] = []
    for item in items:
        name = _first(item, ("name", "profile-name"))
        if not name:
            continue
        rules = item.get("rule") or item.get("rules")
        rule_count = len(rules) if isinstance(rules, list) else None
        out.append(AOS8Policy(name=str(name), rule_count=rule_count, raw=item))
    return out


def parse_export(export: dict[str, Any]) -> dict[str, list[Any]]:
    """Parse an `aos8_export_all()`-shaped dict into normalized object lists."""
    if not isinstance(export, dict):
        return {
            "wlans": [],
            "roles": [],
            "vlans": [],
            "ap_groups": [],
            "controllers": [],
            "policies": [],
        }
    return {
        "wlans": parse_wlans(export),
        "roles": parse_roles(export),
        "vlans": parse_vlans(export),
        "ap_groups": parse_ap_groups(export),
        "controllers": parse_controllers(export),
        "policies": parse_policies(export),
    }
