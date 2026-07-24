"""Pure-python tests for AOS8 export parsing (no MCP/network dependency)."""

from __future__ import annotations

from pipeline.aos8_parsers import (
    parse_ap_groups,
    parse_controllers,
    parse_export,
    parse_policies,
    parse_roles,
    parse_vlans,
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
    "policies": [{"name": "corp-acl", "rule": [1, 2, 3]}],
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
    assert policies[0].rule_count == 3


def test_parse_export_returns_all_object_types():
    parsed = parse_export(_EXPORT)
    assert set(parsed) == {"wlans", "roles", "vlans", "ap_groups", "controllers", "policies"}
    assert len(parsed["wlans"]) == 3
    assert len(parsed["roles"]) == 2


def test_parse_export_tolerates_non_dict_input():
    parsed = parse_export(None)  # type: ignore[arg-type]
    assert parsed["wlans"] == []
    assert parsed["roles"] == []
