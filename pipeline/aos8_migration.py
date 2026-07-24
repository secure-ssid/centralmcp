"""Deterministic, pure-python AOS8 migration candidate planning.

This module performs no target writes. It emits ordered candidate IR for later
Classic/New Central adapters, explicit dependency references, and warnings or
`unsupported_fields` entries for every source field that is not normalized.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pipeline.aos8_parsers import parse_export_report
from pipeline.aos8_schema import (
    AOS8VRRP,
    UNSUPPORTED_FIELDS,
    AOS8AAAProfile,
    AOS8ApGroup,
    AOS8AuthProfile,
    AOS8AuthServer,
    AOS8Controller,
    AOS8Policy,
    AOS8Role,
    AOS8Route,
    AOS8ServerGroup,
    AOS8Vlan,
    AOS8Wlan,
    ClassicCentralCandidate,
    NewCentralCandidate,
)

APPLY_ORDER = {
    "vlan": 10,
    "auth_server": 10,
    "dot1x_auth_profile": 20,
    "mac_auth_profile": 20,
    "server_group": 20,
    "policy": 20,
    "role": 30,
    "aaa_profile": 40,
    "wlan": 50,
    "ap_group": 60,
    "route": 70,
    "vrrp": 80,
    "controller": 90,
}

_SECRET_MARKER = "<redacted:present>"
_EMPTY_SECRET_MARKER = "<redacted:empty>"
_SENSITIVE_EXACT_KEYS = {
    "access_token",
    "admin_dn",
    "admin_password",
    "admin_passwd",
    "adminpwd",
    "bind_credential",
    "bind_credentials",
    "bind_dn",
    "bind_password",
    "bind_passwd",
    "bind_username",
    "bindpwd",
    "api_key",
    "api_token",
    "client_secret",
    "cppm_username_password",
    "credential",
    "credentials",
    "key",
    "ldap_admindn",
    "ldap_admin_dn",
    "ldap_adminpasswd",
    "ldap_adminpwd",
    "password",
    "passphrase",
    "passwd",
    "private_key",
    "presharedkey",
    "psk",
    "pwd",
    "rad_key",
    "radkey",
    "radius_key",
    "radiuskey",
    "radius_secret",
    "secret",
    "sharedkey",
    "sharedsecret",
    "shared_key",
    "shared_secret",
    "tacacs_key",
    "tacacskey",
    "tacacs_secret",
    "token",
}
_SENSITIVE_KEY_PREFIXES = (
    "api_",
    "auth_",
    "client_",
    "credential_",
    "encryption_",
    "private_",
    "preshared_",
    "pre_shared_",
    "rad_",
    "radius_",
    "shared_",
    "secret_",
    "tacacs_",
)


def _normalized_key(key: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(key).lower())).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    if normalized.endswith(
        ("_password", "_passwd", "_pwd", "_passphrase", "_secret")
    ):
        return True
    return normalized.endswith("_key") and normalized.startswith(_SENSITIVE_KEY_PREFIXES)


def _redact_sensitive_values(
    value: Any,
    path: str = "",
) -> tuple[Any, list[str]]:
    """Return a JSON-safe copy with credential values replaced by stable markers."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        secret_fields: list[str] = []
        for key in sorted(value):
            field_path = f"{path}.{key}" if path else str(key)
            field_value = value[key]
            if _is_sensitive_key(key):
                redacted[key] = (
                    _SECRET_MARKER
                    if field_value not in (None, "", [], {})
                    else _EMPTY_SECRET_MARKER
                )
                secret_fields.append(field_path)
                continue
            redacted_value, nested_fields = _redact_sensitive_values(
                field_value, field_path
            )
            redacted[key] = redacted_value
            secret_fields.extend(nested_fields)
        return redacted, secret_fields
    if isinstance(value, list):
        redacted_items: list[Any] = []
        secret_fields: list[str] = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            redacted_item, nested_fields = _redact_sensitive_values(item, item_path)
            redacted_items.append(redacted_item)
            secret_fields.extend(nested_fields)
        return redacted_items, secret_fields
    if isinstance(value, tuple):
        redacted_items, secret_fields = _redact_sensitive_values(list(value), path)
        return redacted_items, secret_fields
    return value, []


def _sorted_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


def _sorted_items(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    return sorted(payload.items(), key=lambda pair: pair[0])


def _diff_entry(source: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    safe_source, _ = _redact_sensitive_values(source)
    safe_candidate, _ = _redact_sensitive_values(candidate)
    return {
        "source": _sorted_items(safe_source),
        "candidate": _sorted_items(safe_candidate),
    }


def _remaining(raw: dict[str, Any], mapped_keys: set[str]) -> dict[str, Any]:
    return {key: raw[key] for key in sorted(raw) if key not in mapped_keys}


def _unsupported_warnings(
    object_type: str,
    identifier: str,
    unsupported: dict[str, Any],
    secret_fields: list[str],
) -> list[str]:
    secret_roots = {
        path.removeprefix("unsupported_fields.").split(".", 1)[0].split("[", 1)[0]
        for path in secret_fields
        if path.startswith("unsupported_fields.")
    }
    return [
        (
            f"{object_type}:{identifier}: source field {field!r} is not mapped; "
            "its exact value is retained in `unsupported_fields`."
        )
        for field in sorted(unsupported)
        if field not in secret_roots
    ]


def _dependency(object_type: str, identifier: Any) -> str | None:
    if identifier in (None, ""):
        return None
    return f"{object_type}:{identifier}"


def _dependencies(*values: str | None) -> list[str]:
    return sorted({value for value in values if value})


def _candidate(
    candidate_class: type[ClassicCentralCandidate] | type[NewCentralCandidate],
    object_type: str,
    identifier: str,
    payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    dependencies: list[str] | None = None,
    unsupported_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_payload, payload_secret_fields = _redact_sensitive_values(payload, "payload")
    safe_unsupported, unsupported_secret_fields = _redact_sensitive_values(
        _sorted_mapping(unsupported_fields or {}),
        "unsupported_fields",
    )
    secret_fields = sorted(set([*payload_secret_fields, *unsupported_secret_fields]))
    credential_warnings = [
        (
            f"{object_type}:{identifier}: credential field {field!r} was redacted; "
            "re-enter this credential on the target before apply."
        )
        for field in secret_fields
    ]
    local_warnings = sorted(
        set(
            [
                *(warnings or []),
                *_unsupported_warnings(
                    object_type,
                    identifier,
                    safe_unsupported,
                    secret_fields,
                ),
                *credential_warnings,
            ]
        )
    )
    return candidate_class(
        object_type=object_type,
        identifier=identifier,
        payload=safe_payload,
        warnings=local_warnings,
        dependencies=sorted(set(dependencies or [])),
        apply_order=APPLY_ORDER[object_type],
        unsupported_fields=safe_unsupported,
        requires_secret_input=bool(secret_fields),
        secret_fields=secret_fields,
    ).to_dict()


def _append_for_both(
    classic: list[dict[str, Any]],
    new: list[dict[str, Any]],
    object_type: str,
    identifier: str,
    payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    dependencies: list[str] | None = None,
    unsupported_fields: dict[str, Any] | None = None,
) -> list[str]:
    classic_candidate = _candidate(
        ClassicCentralCandidate,
        object_type,
        identifier,
        payload,
        warnings=warnings,
        dependencies=dependencies,
        unsupported_fields=unsupported_fields,
    )
    new_candidate = _candidate(
        NewCentralCandidate,
        object_type,
        identifier,
        payload,
        warnings=warnings,
        dependencies=dependencies,
        unsupported_fields=unsupported_fields,
    )
    classic.append(classic_candidate)
    new.append(new_candidate)
    return [*classic_candidate["warnings"], *new_candidate["warnings"]]


def _wlan_payload(
    wlan: AOS8Wlan,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    warnings: list[str] = []
    payload = {
        "name": wlan.profile_name,
        "essid": wlan.essid or wlan.profile_name,
        "vlan": wlan.vlan,
        "aaa_profile": wlan.aaa_profile,
        "virtual_ap_profile": wlan.virtual_ap_profile,
    }
    unsupported: dict[str, Any] = {}
    if wlan.opmode is not None:
        unsupported["ssid_profile.opmode"] = wlan.opmode
        warnings.append(f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['opmode']}")
    if wlan.forward_mode is not None:
        unsupported["virtual_ap.forward_mode"] = wlan.forward_mode
        warnings.append(
            f"wlan:{wlan.profile_name}: {UNSUPPORTED_FIELDS['wlan']['forward_mode']}"
        )
    ssid_raw = wlan.raw.get("ssid_profile", {})
    vap_raw = wlan.raw.get("virtual_ap", {})
    if isinstance(ssid_raw, dict):
        unsupported.update(
            {
                f"ssid_profile.{key}": value
                for key, value in _remaining(
                    ssid_raw,
                    {"profile-name", "name", "essid", "ESSID", "opmode"},
                ).items()
            }
        )
    if isinstance(vap_raw, dict):
        unsupported.update(
            {
                f"virtual_ap.{key}": value
                for key, value in _remaining(
                    vap_raw,
                    {
                        "profile-name",
                        "name",
                        "ssid-profile",
                        "ssid_prof",
                        "vlan",
                        "aaa-profile",
                        "aaa_prof",
                        "forward-mode",
                        "forward_mode",
                    },
                ).items()
            }
        )
    return payload, warnings, unsupported


def _role_payload(
    role: AOS8Role,
    *,
    new_central: bool,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    payload: dict[str, Any] = {"name": role.rolename, "vlan": role.vlan}
    if role.acl is not None:
        payload["policies" if new_central else "acl"] = role.acl
    warnings: list[str] = []
    unsupported = _remaining(
        role.raw,
        {
            "rolename",
            "role",
            "name",
            "profile-name",
            "vlan",
            "acl",
            "access-list",
            "captive-portal-profile",
        },
    )
    if role.captive_portal_profile is not None:
        unsupported["captive-portal-profile"] = role.captive_portal_profile
        warnings.append(
            f"role:{role.rolename}: {UNSUPPORTED_FIELDS['role']['captive_portal_profile']}"
        )
    return payload, warnings, unsupported


def _policy_payload(
    policy: AOS8Policy,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    unsupported = _remaining(
        policy.raw,
        {
            "accname",
            "name",
            "profile-name",
            "rule",
            "rules",
            "acl_sess__v4policy",
            "acl_sess__v6policy",
        },
    )
    warnings: list[str] = []
    for family_rules in (policy.ipv4_rules, policy.ipv6_rules):
        for index, rule in enumerate(family_rules):
            rules.append(
                {
                    "address_family": rule.address_family,
                    "source": rule.source,
                    "destination": rule.destination,
                    "service": rule.service,
                    "action": rule.action,
                    "log": rule.log,
                }
            )
            for key, value in rule.unsupported_fields.items():
                field = f"{rule.address_family}_rules[{index}].{key}"
                unsupported[field] = value
                warnings.append(
                    f"policy:{policy.name}: {field}: "
                    f"{UNSUPPORTED_FIELDS['policy']['unsupported_rule_field']}"
                )
    return (
        {"name": policy.name, "rule_count": policy.rule_count, "rules": rules},
        warnings,
        unsupported,
    )


def _aaa_payload(profile: AOS8AAAProfile) -> dict[str, Any]:
    return {
        "name": profile.profile_name,
        "default_user_role": profile.default_user_role,
        "dot1x_auth_profile": profile.dot1x_auth_profile,
        "dot1x_default_role": profile.dot1x_default_role,
        "dot1x_server_group": profile.dot1x_server_group,
        "mac_auth_profile": profile.mac_auth_profile,
        "mac_default_role": profile.mac_default_role,
        "mac_server_group": profile.mac_server_group,
        "accounting_server_group": profile.accounting_server_group,
    }


def _route_identifier(route: AOS8Route, index: int) -> str:
    destination = route.destination or f"unknown-{index}"
    mask = f"/{route.netmask}" if route.netmask else ""
    next_hop = route.next_hop or "unknown"
    return f"{route.address_family}:{destination}{mask}->{next_hop}"


def _vrrp_identifier(vrrp: AOS8VRRP, index: int) -> str:
    vrid = vrrp.vrid if vrrp.vrid is not None else f"unknown-{index}"
    vlan = vrrp.vlan_id if vrrp.vlan_id is not None else "none"
    return f"{vrrp.address_family}:{vrid}@{vlan}"


def _policy_dependencies(acl: Any) -> list[str]:
    if isinstance(acl, list):
        values = acl
    elif acl in (None, ""):
        values = []
    else:
        values = [acl]
    return _dependencies(*(_dependency("policy", value) for value in values))


def _default_verification_plan(config_path: str) -> list[dict[str, Any]]:
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
    """Turn an `aos8_export_all()` export into stable, dependency-ordered IR."""
    parsed, parse_warnings = parse_export_report(export)
    config_path = export.get("config_path", "/md") if isinstance(export, dict) else "/md"
    if not isinstance(config_path, str):
        parse_warnings.append("export: config_path is malformed; using '/md'.")
        config_path = "/md"

    classic: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []
    warnings = list(parse_warnings)
    if isinstance(export, dict):
        source_warnings = export.get("warnings", [])
        if isinstance(source_warnings, list):
            warnings.extend(f"export: {warning}" for warning in source_warnings if warning)
        elif source_warnings:
            warnings.append("export: warnings field was malformed and could not be parsed.")
    diff: dict[str, Any] = {}

    server_ids_by_name: dict[str, list[str]] = {}
    for server in parsed["auth_servers"]:
        assert isinstance(server, AOS8AuthServer)
        identifier = f"{server.server_type}:{server.name}"
        payload = {
            "name": server.name,
            "server_type": server.server_type,
            "host": server.host,
        }
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "auth_server",
                identifier,
                payload,
                unsupported_fields=server.settings,
            )
        )
        server_ids_by_name.setdefault(server.name, []).append(
            f"auth_server:{identifier}"
        )
        diff[f"auth_server:{identifier}"] = _diff_entry(server.to_dict(), payload)

    for vlan in parsed["vlans"]:
        assert isinstance(vlan, AOS8Vlan)
        identifier = str(vlan.vlan_id)
        payload = {"vlan_id": vlan.vlan_id, "description": vlan.description}
        unsupported = _remaining(
            vlan.raw,
            {"id", "vlan-id", "vlan_id", "name", "description"},
        )
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "vlan",
                identifier,
                payload,
                unsupported_fields=unsupported,
            )
        )
        diff[f"vlan:{identifier}"] = _diff_entry(vlan.to_dict(), payload)

    for profile in parsed["dot1x_auth_profiles"] + parsed["mac_auth_profiles"]:
        assert isinstance(profile, AOS8AuthProfile)
        object_type = f"{profile.auth_type}_auth_profile"
        payload = {"name": profile.profile_name, "auth_type": profile.auth_type}
        warnings.extend(
            _append_for_both(
                classic,
                new,
                object_type,
                profile.profile_name,
                payload,
                unsupported_fields=profile.settings,
            )
        )
        diff[f"{object_type}:{profile.profile_name}"] = _diff_entry(
            profile.to_dict(), payload
        )

    for group in parsed["server_groups"]:
        assert isinstance(group, AOS8ServerGroup)
        dependencies = sorted(
            {
                dependency
                for server_name in group.auth_servers
                for dependency in server_ids_by_name.get(server_name, [])
            }
        )
        unresolved = sorted(
            name for name in group.auth_servers if name not in server_ids_by_name
        )
        local_warnings = [
            (
                f"server_group:{group.name}: referenced auth server {name!r} was "
                "not present in the export; dependency cannot be resolved."
            )
            for name in unresolved
        ]
        payload = {
            "name": group.name,
            "auth_servers": sorted(group.auth_servers),
            "auth_server_entries": group.auth_server_entries,
            "fail_through": group.fail_through,
            "load_balance": group.load_balance,
            "derivation_rules": group.derivation_rules,
        }
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "server_group",
                group.name,
                payload,
                warnings=local_warnings,
                dependencies=dependencies,
                unsupported_fields=group.settings,
            )
        )
        diff[f"server_group:{group.name}"] = _diff_entry(group.to_dict(), payload)

    for policy in parsed["policies"]:
        assert isinstance(policy, AOS8Policy)
        payload, local_warnings, unsupported = _policy_payload(policy)
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "policy",
                policy.name,
                payload,
                warnings=local_warnings,
                unsupported_fields=unsupported,
            )
        )
        diff[f"policy:{policy.name}"] = _diff_entry(policy.to_dict(), payload)

    for role in parsed["roles"]:
        assert isinstance(role, AOS8Role)
        dependencies = _dependencies(
            _dependency("vlan", role.vlan), *_policy_dependencies(role.acl)
        )
        classic_payload, classic_warnings, classic_unsupported = _role_payload(
            role, new_central=False
        )
        new_payload, new_warnings, new_unsupported = _role_payload(
            role, new_central=True
        )
        classic_candidate = _candidate(
            ClassicCentralCandidate,
            "role",
            role.rolename,
            classic_payload,
            warnings=classic_warnings,
            dependencies=dependencies,
            unsupported_fields=classic_unsupported,
        )
        new_candidate = _candidate(
            NewCentralCandidate,
            "role",
            role.rolename,
            new_payload,
            warnings=new_warnings,
            dependencies=dependencies,
            unsupported_fields=new_unsupported,
        )
        classic.append(classic_candidate)
        new.append(new_candidate)
        warnings.extend(classic_candidate["warnings"])
        warnings.extend(new_candidate["warnings"])
        diff[f"role:{role.rolename}"] = _diff_entry(role.to_dict(), new_payload)

    for profile in parsed["aaa_profiles"]:
        assert isinstance(profile, AOS8AAAProfile)
        payload = _aaa_payload(profile)
        dependencies = _dependencies(
            _dependency("role", profile.default_user_role),
            _dependency("role", profile.dot1x_default_role),
            _dependency("role", profile.mac_default_role),
            _dependency("dot1x_auth_profile", profile.dot1x_auth_profile),
            _dependency("mac_auth_profile", profile.mac_auth_profile),
            _dependency("server_group", profile.dot1x_server_group),
            _dependency("server_group", profile.mac_server_group),
            _dependency("server_group", profile.accounting_server_group),
        )
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "aaa_profile",
                profile.profile_name,
                payload,
                dependencies=dependencies,
                unsupported_fields=profile.settings,
            )
        )
        diff[f"aaa_profile:{profile.profile_name}"] = _diff_entry(
            profile.to_dict(), payload
        )

    for wlan in parsed["wlans"]:
        assert isinstance(wlan, AOS8Wlan)
        dependencies = _dependencies(
            _dependency("vlan", wlan.vlan),
            _dependency("aaa_profile", wlan.aaa_profile),
        )
        classic_payload, classic_warnings, classic_unsupported = _wlan_payload(wlan)
        new_payload, new_warnings, new_unsupported = _wlan_payload(wlan)
        classic_candidate = _candidate(
            ClassicCentralCandidate,
            "wlan",
            wlan.profile_name,
            classic_payload,
            warnings=classic_warnings,
            dependencies=dependencies,
            unsupported_fields=classic_unsupported,
        )
        new_candidate = _candidate(
            NewCentralCandidate,
            "wlan",
            wlan.profile_name,
            new_payload,
            warnings=new_warnings,
            dependencies=dependencies,
            unsupported_fields=new_unsupported,
        )
        classic.append(classic_candidate)
        new.append(new_candidate)
        warnings.extend(classic_candidate["warnings"])
        warnings.extend(new_candidate["warnings"])
        diff[f"wlan:{wlan.profile_name}"] = _diff_entry(wlan.to_dict(), new_payload)

    vap_to_wlan = {
        wlan.virtual_ap_profile: wlan.profile_name
        for wlan in parsed["wlans"]
        if isinstance(wlan, AOS8Wlan) and wlan.virtual_ap_profile
    }
    for group in parsed["ap_groups"]:
        assert isinstance(group, AOS8ApGroup)
        payload = {
            "name": group.profile_name,
            "wlan_profiles": sorted(group.virtual_ap_profiles),
        }
        dependencies = _dependencies(
            *(
                _dependency("wlan", vap_to_wlan.get(vap, vap))
                for vap in group.virtual_ap_profiles
            )
        )
        unsupported = _remaining(
            group.raw,
            {"profile-name", "name", "virtual-ap", "virtual_ap"},
        )
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "ap_group",
                group.profile_name,
                payload,
                dependencies=dependencies,
                unsupported_fields=unsupported,
            )
        )
        diff[f"ap_group:{group.profile_name}"] = _diff_entry(group.to_dict(), payload)

    for index, route in enumerate(parsed["routes"]):
        assert isinstance(route, AOS8Route)
        identifier = _route_identifier(route, index)
        payload = {
            "address_family": route.address_family,
            "destination": route.destination,
            "netmask": route.netmask,
            "next_hop": route.next_hop,
            "secondary_next_hop": route.secondary_next_hop,
            "vlan_id": route.vlan_id,
            "cost": route.cost,
            "secondary_cost": route.secondary_cost,
            "zero": route.zero,
        }
        dependencies = _dependencies(_dependency("vlan", route.vlan_id))
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "route",
                identifier,
                payload,
                dependencies=dependencies,
                unsupported_fields=route.settings,
            )
        )
        diff[f"route:{identifier}"] = _diff_entry(route.to_dict(), payload)

    for index, vrrp in enumerate(parsed["vrrp"]):
        assert isinstance(vrrp, AOS8VRRP)
        identifier = _vrrp_identifier(vrrp, index)
        payload = {
            "address_family": vrrp.address_family,
            "vrid": vrrp.vrid,
            "virtual_ip": vrrp.virtual_ip,
            "vlan_id": vrrp.vlan_id,
            "priority": vrrp.priority,
            "preempt": vrrp.preempt,
            "shutdown": vrrp.shutdown,
            "advertisement_interval": vrrp.advertisement_interval,
            "hold_time": vrrp.hold_time,
            "description": vrrp.description,
            "authentication": vrrp.authentication,
            "tracking": _sorted_mapping(vrrp.tracking),
        }
        dependencies = _dependencies(_dependency("vlan", vrrp.vlan_id))
        warnings.extend(
            _append_for_both(
                classic,
                new,
                "vrrp",
                identifier,
                payload,
                dependencies=dependencies,
                unsupported_fields=vrrp.settings,
            )
        )
        diff[f"vrrp:{identifier}"] = _diff_entry(vrrp.to_dict(), payload)

    for controller in parsed["controllers"]:
        assert isinstance(controller, AOS8Controller)
        identifier = controller.name or controller.ip_address or "unknown"
        payload = {
            "name": controller.name,
            "ip_address": controller.ip_address,
            "model": controller.model,
            "version": controller.version,
        }
        unsupported = _remaining(
            controller.raw,
            {
                "Name",
                "name",
                "hostname",
                "IP Address",
                "ip_address",
                "Model",
                "model",
                "Version",
                "version",
            },
        )
        classic_candidate = _candidate(
            ClassicCentralCandidate,
            "controller",
            identifier,
            payload,
            unsupported_fields=unsupported,
        )
        classic.append(classic_candidate)
        warnings.extend(classic_candidate["warnings"])
        warnings.append(
            f"controller:{identifier}: AOS8 controllers/Mobility Conductors are not "
            "migrated as New Central objects; onboard replacement gateways/APs individually."
        )
        diff[f"controller:{identifier}"] = _diff_entry(controller.to_dict(), payload)

    def sort_key(candidate: dict[str, Any]) -> tuple[int, str, str]:
        serialized_payload = json.dumps(
            candidate["payload"], sort_keys=True, default=str
        )
        return (
            candidate["apply_order"],
            candidate["object_type"],
            f"{candidate['identifier']}:{serialized_payload}",
        )

    for candidates in (classic, new):
        candidate_keys = {
            f"{candidate['object_type']}:{candidate['identifier']}"
            for candidate in candidates
        }
        for candidate in candidates:
            missing = [
                dependency
                for dependency in candidate["dependencies"]
                if dependency not in candidate_keys
            ]
            for dependency in missing:
                warning = (
                    f"{candidate['object_type']}:{candidate['identifier']}: dependency "
                    f"{dependency!r} is not present in this export; the target adapter "
                    "must resolve it as a built-in/external prerequisite or block apply."
                )
                candidate["warnings"] = sorted(set([*candidate["warnings"], warning]))
                warnings.append(warning)
    classic.sort(key=sort_key)
    new.sort(key=sort_key)
    return {
        "config_path": config_path,
        "candidates": {"classic_central": classic, "new_central": new},
        "warnings": sorted(set(warnings)),
        "diff": dict(sorted(diff.items())),
        "verification_plan": _default_verification_plan(config_path),
        "source_object_counts": {
            key: len(value) for key, value in sorted(parsed.items())
        },
    }
