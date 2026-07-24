"""Pure-python tests for deterministic AOS8 migration planning (no MCP/network)."""

from __future__ import annotations

import json

from pipeline.aos8_migration import _is_sensitive_key, build_migration_plan

_EXPORT = {
    "config_path": "/md/lab",
    "wlans": {
        "ssid_profiles": [{"profile-name": "Corp", "essid": "Corp", "opmode": "wpa2-aes"}],
        "virtual_aps": [
            {
                "profile-name": "Corp-VAP",
                "ssid-profile": "Corp",
                "aaa-profile": "corp-aaa",
                "vlan": 20,
                "forward-mode": "tunnel",
            }
        ],
    },
    "roles": [
        {"role": "employee", "acl": "allowall", "vlan": 20, "captive-portal-profile": "none"}
    ],
    "vlans": [{"id": 20, "description": "Corp"}],
    "ap_groups": [{"profile-name": "Lab-AP-Group", "virtual-ap": ["Corp-VAP"]}],
    "controllers": [{"Name": "mc1", "IP Address": "10.0.0.1", "Model": "7210"}],
    "policies": [
        {
            "accname": "corp-acl",
            "acl_sess__v4policy": [
                {
                    "source": "user",
                    "destination": "any",
                    "service": "https",
                    "action": "permit",
                    "time-range": "business-hours",
                }
            ],
        }
    ],
    "aaa": {
        "aaa_profiles": [
            {
                "profile-name": "corp-aaa",
                "default_user_role": "employee",
                "dot1x_auth_profile": "corp-dot1x",
                "dot1x_server_group": "corp-sg",
                "enforce_dhcp": True,
                "shared-secret": "aaa-profile-secret",
            }
        ],
        "dot1x_auth_profiles": [
            {
                "profile-name": "corp-dot1x",
                "reauthentication": True,
                "keycache_tmout": 600,
                "server_cert": "corp-server-cert",
                "use_session_key": True,
                "password": "dot1x-profile-secret",
            }
        ],
        "mac_auth_profiles": [
            {"profile-name": "corp-mac", "mac_reauthentication": True}
        ],
        "server_groups": [
            {
                "sg_name": "corp-sg",
                "auth_server": [{"name": "rad1", "position": 1}],
                "fail_thru": True,
            }
        ],
        "radius_servers": [
            {
                "rad_server_name": "rad1",
                "rad_host": "10.0.0.10",
                "rad_authport": 1812,
                "rad_key": "radius-shared-secret",
                "cppm_username_password": "cppm-combined-secret",
            }
        ],
        "ldap_servers": [
            {
                "ldap_server_name": "ldap1",
                "ldap_host": "10.0.0.11",
                "ldap_admindn": "cn=bind-user,dc=example,dc=com",
                "ldap_adminpasswd": "ldap-bind-secret",
                "ldap_keyattribute": "sAMAccountName",
            }
        ],
        "tacacs_servers": [
            {
                "tacacs_server_name": "tac1",
                "tacacs_host": "10.0.0.12",
                "tacacs_key": "tacacs-shared-secret",
                "tacacs_timeout": 5,
            }
        ],
    },
    "routing": {
        "ipv4_routes": [
            {
                "destip": "10.20.0.0",
                "destmask": "255.255.0.0",
                "nexthop": "10.0.0.254",
                "cost": 10,
                "zero": 0,
            }
        ],
        "ipv6_routes": [
            {
                "destip": "2001:db8:20::/64",
                "nexthop": "2001:db8::1",
                "nexthop1": "2001:db8::2",
                "vlanid": 20,
                "cost": 20,
                "cost1": 30,
                "zero": 0,
            }
        ],
        "vrrp": [
            {
                "id": 20,
                "vrrp_ip": "10.0.20.1",
                "vrrp_vlan": 20,
                "vrrp_priority": 110,
                "vrrp_preempt": True,
            }
        ],
        "vrrp6": [],
    },
}


def test_build_migration_plan_is_deterministic():
    first = build_migration_plan(_EXPORT)
    second = build_migration_plan(_EXPORT)
    assert first == second


def test_build_migration_plan_surfaces_partial_export_warnings():
    export = {
        "config_path": "/md",
        "wlans": {"ssid_profiles": "bad", "virtual_aps": []},
        "roles": [],
        "vlans": [],
        "ap_groups": [],
        "controllers": [],
        "policies": [None],
        "warnings": ["controllers: HTTP 503"],
    }

    plan = build_migration_plan(export)

    assert "export: controllers: HTTP 503" in plan["warnings"]
    assert (
        "export: wlans.ssid_profiles section is missing or malformed."
        in plan["warnings"]
    )
    assert "export: policies[0] is not an object and was not parsed." in plan["warnings"]


def test_build_migration_plan_produces_classic_and_new_central_candidates():
    plan = build_migration_plan(_EXPORT)
    classic_types = {c["object_type"] for c in plan["candidates"]["classic_central"]}
    new_types = {c["object_type"] for c in plan["candidates"]["new_central"]}
    assert classic_types == {
        "wlan",
        "role",
        "vlan",
        "ap_group",
        "controller",
        "policy",
        "aaa_profile",
        "dot1x_auth_profile",
        "mac_auth_profile",
        "server_group",
        "auth_server",
        "route",
        "vrrp",
    }
    # Controllers have no New Central object equivalent, by design.
    assert new_types == classic_types - {"controller"}


def test_build_migration_plan_warns_on_lossy_wlan_and_role_fields():
    plan = build_migration_plan(_EXPORT)
    joined = " ".join(plan["warnings"])
    assert "opmode" in joined
    assert "captive-portal" in joined
    assert "controllers/Mobility Conductors are not" in joined


def test_build_migration_plan_diff_has_sorted_source_and_candidate_keys():
    plan = build_migration_plan(_EXPORT)
    wlan_diff = plan["diff"]["wlan:Corp"]
    source_keys = [key for key, _ in wlan_diff["source"]]
    candidate_keys = [key for key, _ in wlan_diff["candidate"]]
    assert source_keys == sorted(source_keys)
    assert candidate_keys == sorted(candidate_keys)


def test_build_migration_plan_verification_plan_references_real_tool_names_only():
    plan = build_migration_plan(_EXPORT)
    tool_names = {step["tool"] for step in plan["verification_plan"]}
    assert tool_names == {"list_overlay_wlans", "list_roles", "list_named_vlans", "list_devices"}
    for step in plan["verification_plan"]:
        assert "purpose" in step
        assert "args" in step


def test_build_migration_plan_source_object_counts_match_input():
    plan = build_migration_plan(_EXPORT)
    assert plan["source_object_counts"] == {
        "aaa_profiles": 1,
        "ap_groups": 1,
        "auth_servers": 3,
        "controllers": 1,
        "dot1x_auth_profiles": 1,
        "mac_auth_profiles": 1,
        "policies": 1,
        "roles": 1,
        "routes": 2,
        "server_groups": 1,
        "vlans": 1,
        "vrrp": 1,
        "wlans": 1,
    }


def test_build_migration_plan_orders_dependencies_before_dependents():
    plan = build_migration_plan(_EXPORT)
    candidates = plan["candidates"]["new_central"]
    assert [candidate["apply_order"] for candidate in candidates] == sorted(
        candidate["apply_order"] for candidate in candidates
    )
    by_key = {
        f"{candidate['object_type']}:{candidate['identifier']}": candidate
        for candidate in candidates
    }
    assert by_key["server_group:corp-sg"]["dependencies"] == [
        "auth_server:radius:rad1"
    ]
    assert by_key["server_group:corp-sg"]["payload"]["auth_server_entries"] == [
        {"name": "rad1", "position": 1}
    ]
    assert by_key["aaa_profile:corp-aaa"]["dependencies"] == [
        "dot1x_auth_profile:corp-dot1x",
        "role:employee",
        "server_group:corp-sg",
    ]
    assert by_key["wlan:Corp"]["dependencies"] == [
        "aaa_profile:corp-aaa",
        "vlan:20",
    ]


def test_build_migration_plan_preserves_unmapped_fields_and_policy_details():
    plan = build_migration_plan(_EXPORT)
    candidates = plan["candidates"]["classic_central"]
    aaa = next(candidate for candidate in candidates if candidate["object_type"] == "aaa_profile")
    radius = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "auth_server"
        and candidate["payload"]["server_type"] == "radius"
    )
    policy = next(candidate for candidate in candidates if candidate["object_type"] == "policy")

    assert aaa["unsupported_fields"] == {
        "enforce_dhcp": True,
        "shared-secret": "<redacted:present>",
    }
    assert radius["unsupported_fields"] == {
        "cppm_username_password": "<redacted:present>",
        "rad_authport": 1812,
        "rad_key": "<redacted:present>",
    }
    assert policy["payload"]["rules"][0]["service"] == "https"
    assert policy["unsupported_fields"] == {
        "ipv4_rules[0].time-range": "business-hours"
    }
    ipv6_route = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "route"
        and candidate["payload"]["address_family"] == "ipv6"
    )
    vrrp = next(candidate for candidate in candidates if candidate["object_type"] == "vrrp")
    assert ipv6_route["payload"]["secondary_next_hop"] == "2001:db8::2"
    assert ipv6_route["dependencies"] == ["vlan:20"]
    assert vrrp["payload"]["priority"] == 110
    assert vrrp["dependencies"] == ["vlan:20"]
    assert any("exact value is retained" in warning for warning in plan["warnings"])


def test_build_migration_plan_never_serializes_auth_secrets():
    plan = build_migration_plan(_EXPORT)
    serialized = json.dumps(plan, sort_keys=True)
    secret_values = {
        "aaa-profile-secret",
        "dot1x-profile-secret",
        "radius-shared-secret",
        "cppm-combined-secret",
        "cn=bind-user,dc=example,dc=com",
        "ldap-bind-secret",
        "tacacs-shared-secret",
    }
    assert all(secret not in serialized for secret in secret_values)

    candidates = plan["candidates"]["classic_central"]
    radius = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "auth_server"
        and candidate["payload"]["server_type"] == "radius"
    )
    ldap = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "auth_server"
        and candidate["payload"]["server_type"] == "ldap"
    )
    tacacs = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "auth_server"
        and candidate["payload"]["server_type"] == "tacacs"
    )
    dot1x = next(
        candidate
        for candidate in candidates
        if candidate["object_type"] == "dot1x_auth_profile"
    )

    assert radius["requires_secret_input"] is True
    assert radius["secret_fields"] == [
        "unsupported_fields.cppm_username_password",
        "unsupported_fields.rad_key",
    ]
    assert ldap["unsupported_fields"]["ldap_admindn"] == "<redacted:present>"
    assert ldap["unsupported_fields"]["ldap_keyattribute"] == "sAMAccountName"
    assert tacacs["unsupported_fields"]["tacacs_key"] == "<redacted:present>"
    assert tacacs["unsupported_fields"]["tacacs_timeout"] == 5
    assert dot1x["unsupported_fields"]["password"] == "<redacted:present>"
    assert dot1x["unsupported_fields"]["keycache_tmout"] == 600
    assert dot1x["unsupported_fields"]["server_cert"] == "corp-server-cert"
    assert dot1x["unsupported_fields"]["use_session_key"] is True
    assert any("re-enter this credential" in warning for warning in plan["warnings"])


def test_sensitive_key_detection_covers_credentials_without_false_positives():
    for key in (
        "rad_key",
        "radius-shared-secret",
        "ldap_adminpasswd",
        "ldap-admin-dn",
        "bind_password",
        "tacacsKey",
        "sharedSecret",
        "client_secret",
        "api-token",
        "pwd",
    ):
        assert _is_sensitive_key(key)

    for key in (
        "keycache_tmout",
        "ldap_keyattribute",
        "server_cert",
        "token_caching_period",
        "use_session_key",
        "wpa_key_retries",
    ):
        assert not _is_sensitive_key(key)


def test_build_migration_plan_handles_empty_export():
    plan = build_migration_plan({})
    assert plan["candidates"]["classic_central"] == []
    assert plan["candidates"]["new_central"] == []
    assert "export: wlans section is missing or malformed." in plan["warnings"]
    assert "export: roles section is missing or malformed." in plan["warnings"]
    assert plan["source_object_counts"] == {
        "aaa_profiles": 0,
        "ap_groups": 0,
        "auth_servers": 0,
        "controllers": 0,
        "dot1x_auth_profiles": 0,
        "mac_auth_profiles": 0,
        "policies": 0,
        "roles": 0,
        "routes": 0,
        "server_groups": 0,
        "vlans": 0,
        "vrrp": 0,
        "wlans": 0,
    }
