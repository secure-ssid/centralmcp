"""Pure-python tests for deterministic AOS8 migration planning (no MCP/network)."""

from __future__ import annotations

from pipeline.aos8_migration import build_migration_plan

_EXPORT = {
    "config_path": "/md/lab",
    "wlans": {
        "ssid_profiles": [{"profile-name": "Corp", "essid": "Corp", "opmode": "wpa2-aes"}],
        "virtual_aps": [
            {
                "profile-name": "Corp-VAP",
                "ssid-profile": "Corp",
                "aaa-profile": "dot1x",
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
    "policies": [{"name": "corp-acl", "rule": [1, 2, 3]}],
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
    assert "export: wlans.ssid_profiles is missing or malformed." in plan["warnings"]
    assert "export: policies dropped 1 malformed item(s)." in plan["warnings"]


def test_build_migration_plan_produces_classic_and_new_central_candidates():
    plan = build_migration_plan(_EXPORT)
    classic_types = {c["object_type"] for c in plan["candidates"]["classic_central"]}
    new_types = {c["object_type"] for c in plan["candidates"]["new_central"]}
    assert classic_types == {"wlan", "role", "vlan", "ap_group", "controller", "policy"}
    # Controllers have no New Central object equivalent, by design.
    assert new_types == {"wlan", "role", "vlan", "ap_group", "policy"}


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
        "ap_groups": 1,
        "controllers": 1,
        "policies": 1,
        "roles": 1,
        "vlans": 1,
        "wlans": 1,
    }


def test_build_migration_plan_handles_empty_export():
    plan = build_migration_plan({})
    assert plan["candidates"]["classic_central"] == []
    assert plan["candidates"]["new_central"] == []
    assert "export: wlans section is missing or malformed." in plan["warnings"]
    assert "export: roles section is missing or malformed." in plan["warnings"]
    assert plan["source_object_counts"] == {
        "ap_groups": 0,
        "controllers": 0,
        "policies": 0,
        "roles": 0,
        "vlans": 0,
        "wlans": 0,
    }
