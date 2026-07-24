"""Pure-python tests for AOS8 export parsing (no MCP/network dependency)."""

from __future__ import annotations

from pipeline.aos8_parsers import (
    parse_aaa_profiles,
    parse_ap_groups,
    parse_auth_profiles,
    parse_auth_servers,
    parse_controllers,
    parse_export,
    parse_export_report,
    parse_policies,
    parse_roles,
    parse_routes,
    parse_server_groups,
    parse_vlans,
    parse_vrrp,
    parse_wlans,
)

_EXPORT = {
    "config_path": "/md/lab",
    "wlans": {
        "ssid_profiles": [
            {"profile-name": "Corp", "essid": "Corp", "opmode": "wpa2-aes"},
            {"profile-name": "Open-Only", "essid": "Open-Only"},
        ],
        "virtual_aps": [
            {
                "profile-name": "Corp-VAP",
                "ssid-profile": "Corp",
                "aaa-profile": "dot1x",
                "vlan": 20,
                "forward-mode": "tunnel",
            },
            {
                "profile-name": "Orphan-VAP",
                "ssid-profile": "Missing-SSID",
                "vlan": 40,
            },
        ],
    },
    "roles": [
        {"role": "employee", "acl": "allowall", "vlan": 20, "captive-portal-profile": "none"},
        {"rolename": "guest", "vlan": 30},
    ],
    "vlans": [{"id": 20, "description": "Corp"}, {"id": 30, "description": "Guest"}],
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
                    "log": True,
                    "time-range": "business-hours",
                }
            ],
            "acl_sess__v6policy": [
                {"src": "any", "dst": "any", "protocol": "icmpv6", "action": "permit"}
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
            }
        ],
        "dot1x_auth_profiles": [
            {"profile-name": "corp-dot1x", "reauthentication": True, "quiet_period": 30}
        ],
        "mac_auth_profiles": [
            {"profile-name": "corp-mac", "mac_reauthentication": True}
        ],
        "server_groups": [
            {
                "sg_name": "corp-sg",
                "auth_server": [{"name": "rad1"}],
                "fail_thru": True,
                "load_balance": "least-outstanding",
            }
        ],
        "radius_servers": [
            {
                "rad_server_name": "rad1",
                "rad_host": "10.0.0.10",
                "rad_authport": 1812,
            }
        ],
        "ldap_servers": [
            {"ldap_server_name": "ldap1", "ldap_host": "10.0.0.11", "ldap_authport": 389}
        ],
        "tacacs_servers": [
            {
                "tacacs_server_name": "tac1",
                "tacacs_host": "10.0.0.12",
                "tacacs_tcpport": 49,
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
                "vrrp_track_vlan": 30,
            }
        ],
        "vrrp6": [
            {
                "id": 21,
                "vrrp6_ip": "2001:db8:20::1",
                "vrrp6_vlan": 20,
                "vrrp6_priority": 105,
            }
        ],
    },
}


def test_parse_wlans_merges_ssid_and_virtual_ap_by_name():
    wlans = parse_wlans(_EXPORT)
    corp = next(w for w in wlans if w.profile_name == "Corp")
    assert corp.opmode == "wpa2-aes"
    assert corp.vlan == 20
    assert corp.forward_mode == "tunnel"
    assert corp.aaa_profile == "dot1x"
    assert corp.virtual_ap_profile == "Corp-VAP"


def test_parse_wlans_keeps_ssid_profile_without_virtual_ap():
    wlans = parse_wlans(_EXPORT)
    open_only = next(w for w in wlans if w.profile_name == "Open-Only")
    assert open_only.vlan is None
    assert open_only.virtual_ap_profile is None


def test_parse_wlans_includes_virtual_ap_with_no_matching_ssid_profile():
    wlans = parse_wlans(_EXPORT)
    names = {w.profile_name for w in wlans}
    assert "Orphan-VAP" in names
    orphan = next(w for w in wlans if w.profile_name == "Orphan-VAP")
    assert orphan.vlan == 40


def test_parse_roles_probes_role_and_rolename_keys():
    roles = parse_roles(_EXPORT)
    names = {r.rolename for r in roles}
    assert names == {"employee", "guest"}
    employee = next(r for r in roles if r.rolename == "employee")
    assert employee.acl == "allowall"
    assert employee.captive_portal_profile == "none"


def test_parse_vlans_extracts_id_and_description():
    vlans = parse_vlans(_EXPORT)
    assert {(v.vlan_id, v.description) for v in vlans} == {(20, "Corp"), (30, "Guest")}


def test_parse_ap_groups_collects_virtual_ap_profiles():
    groups = parse_ap_groups(_EXPORT)
    assert len(groups) == 1
    assert groups[0].profile_name == "Lab-AP-Group"
    assert groups[0].virtual_ap_profiles == ["Corp-VAP"]


def test_parse_controllers_reads_display_field_names():
    controllers = parse_controllers(_EXPORT)
    assert len(controllers) == 1
    assert controllers[0].name == "mc1"
    assert controllers[0].ip_address == "10.0.0.1"
    assert controllers[0].model == "7210"


def test_parse_policies_counts_rules():
    policies = parse_policies(_EXPORT)
    assert len(policies) == 1
    assert policies[0].name == "corp-acl"
    assert policies[0].rule_count == 2
    assert policies[0].ipv4_rules[0].service == "https"
    assert policies[0].ipv4_rules[0].unsupported_fields == {
        "time-range": "business-hours"
    }


def test_parse_aaa_and_auth_objects_preserves_source_settings():
    aaa = parse_aaa_profiles(_EXPORT)[0]
    assert aaa.profile_name == "corp-aaa"
    assert aaa.dot1x_server_group == "corp-sg"
    assert aaa.settings == {"enforce_dhcp": True}

    dot1x = parse_auth_profiles(_EXPORT, "dot1x")[0]
    assert dot1x.settings == {"quiet_period": 30, "reauthentication": True}
    servers = parse_auth_servers(_EXPORT)
    assert {(server.server_type, server.name) for server in servers} == {
        ("radius", "rad1"),
        ("ldap", "ldap1"),
        ("tacacs", "tac1"),
    }
    assert parse_server_groups(_EXPORT)[0].auth_servers == ["rad1"]


def test_parse_routes_and_vrrp_capture_ipv4_and_ipv6_fields():
    routes = parse_routes(_EXPORT)
    assert routes[0].netmask == "255.255.0.0"
    assert routes[1].secondary_next_hop == "2001:db8::2"
    vrrp = parse_vrrp(_EXPORT)
    assert vrrp[0].tracking == {"vlan": 30}
    assert vrrp[1].address_family == "ipv6"


def test_parse_export_returns_all_object_types():
    parsed = parse_export(_EXPORT)
    assert set(parsed) == {
        "wlans",
        "roles",
        "vlans",
        "ap_groups",
        "controllers",
        "policies",
        "aaa_profiles",
        "dot1x_auth_profiles",
        "mac_auth_profiles",
        "server_groups",
        "auth_servers",
        "routes",
        "vrrp",
    }
    assert len(parsed["wlans"]) == 3
    assert len(parsed["roles"]) == 2


def test_parse_export_tolerates_non_dict_input():
    parsed = parse_export(None)  # type: ignore[arg-type]
    assert parsed["wlans"] == []
    assert parsed["roles"] == []


def test_parse_export_report_warns_for_malformed_partial_objects():
    parsed, warnings = parse_export_report(
        {
            "wlans": {"ssid_profiles": [{"essid": "missing-name"}], "virtual_aps": []},
            "roles": [None],
            "vlans": [],
            "ap_groups": [],
            "controllers": [],
            "policies": [{"accname": "bad", "acl_sess__v4policy": "not-a-list"}],
            "aaa": {
                "aaa_profiles": [],
                "dot1x_auth_profiles": [],
                "mac_auth_profiles": [],
                "server_groups": [],
                "radius_servers": [],
                "ldap_servers": [],
                "tacacs_servers": [],
            },
            "routing": {
                "ipv4_routes": [{"cost": 1}],
                "ipv6_routes": [],
                "vrrp": [{"vrrp_ip": "10.0.0.1"}],
                "vrrp6": [],
            },
        }
    )

    assert parsed["wlans"][0].profile_name == "unknown-0"
    assert any("roles[0] is not an object" in warning for warning in warnings)
    assert any("expected a list of rules" in warning for warning in warnings)
    assert any("has no destination" in warning for warning in warnings)
    assert any("has no VRRP ID" in warning for warning in warnings)
