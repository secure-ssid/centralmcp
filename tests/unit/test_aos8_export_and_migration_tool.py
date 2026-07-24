"""Tests for AOS8 export tools (`aos8_get_vlans`, `aos8_get_policies`,
`aos8_export_wlans`, `aos8_export_all`) and the `aos8_migration_plan` tool
that ties them to `pipeline.aos8_migration.build_migration_plan`.
"""

from __future__ import annotations

import asyncio

import mcp_servers.aos8 as aos8


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return self._payload


def _fake_client_for_paths(path_to_payload: dict[str, object]):
    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            for suffix, payload in path_to_payload.items():
                if url.endswith(suffix):
                    return _Resp(payload)
            return _Resp({"error": f"unexpected url {url}"}, status_code=404)

    return _FakeAsyncClient


def test_aos8_get_vlans_lists_vlan_id_objects(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {"/v1/configuration/object/vlan_id": {"vlan_id": [{"id": 20, "description": "Corp"}]}}
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_get_vlans(config_path="/md/lab"))

    assert out["config_path"] == "/md/lab"
    assert out["vlans"]["vlan_id"] == [{"id": 20, "description": "Corp"}]


def test_aos8_get_policies_lists_acl_sess_objects(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {"/v1/configuration/object/acl_sess": {"acl_sess": [{"name": "corp-acl"}]}}
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_get_policies(config_path="/md/lab"))

    assert out["config_path"] == "/md/lab"
    assert out["policies"]["acl_sess"] == [{"name": "corp-acl"}]


def test_aos8_export_wlans_merges_ssid_profiles_and_virtual_aps(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {
            "/v1/configuration/object/ssid_prof": {
                "ssid_prof": [{"profile-name": "Corp", "essid": "Corp"}]
            },
            "/v1/configuration/object/virtual_ap": {
                "virtual_ap": [{"profile-name": "Corp-VAP", "ssid-profile": "Corp", "vlan": 20}]
            },
        }
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_export_wlans(config_path="/md/lab"))

    assert out["config_path"] == "/md/lab"
    assert out["ssid_profiles"] == [{"profile-name": "Corp", "essid": "Corp"}]
    assert out["virtual_aps"] == [{"profile-name": "Corp-VAP", "ssid-profile": "Corp", "vlan": 20}]
    assert "warnings" not in out


def test_aos8_export_wlans_collects_warnings_on_partial_failure(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {"/v1/configuration/object/ssid_prof": {"ssid_prof": [{"profile-name": "Corp"}]}}
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_export_wlans(config_path="/md/lab"))

    assert out["ssid_profiles"] == [{"profile-name": "Corp"}]
    assert out["virtual_aps"] == []
    assert any("virtual_aps" in w for w in out["warnings"])


def test_aos8_export_page_collector_exhausts_local_pages():
    records = [{"id": value} for value in range(5)]

    async def fetch(limit: int, offset: int):
        return {"items": {"items": records[offset : offset + limit]}}

    items, warnings = asyncio.run(
        aos8._aos8_collect_all(
            "items",
            fetch,
            page_size=2,
            max_items=10,
        )
    )

    assert items == records
    assert warnings == []


def test_aos8_export_all_fans_out_and_shapes_result(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {
            "/v1/configuration/object/ssid_prof": {
                "ssid_prof": [{"profile-name": "Corp", "essid": "Corp"}]
            },
            "/v1/configuration/object/virtual_ap": {
                "virtual_ap": [{"profile-name": "Corp-VAP", "ssid-profile": "Corp", "vlan": 20}]
            },
            "/v1/configuration/object/role": {
                "role": [{"role": "employee", "vlan": 20}]
            },
            "/v1/configuration/object/vlan_id": {"vlan_id": [{"id": 20, "description": "Corp"}]},
            "/v1/configuration/object/ap_group": {
                "ap_group": [{"profile-name": "Lab-AP-Group"}]
            },
            "/v1/configuration/showcommand": {
                "Switches": [{"Name": "mc1", "IP Address": "10.0.0.1"}]
            },
            "/v1/configuration/object/acl_sess": {"acl_sess": [{"name": "corp-acl"}]},
            "/v1/configuration/object/aaa_prof": {
                "aaa_prof": [
                    {
                        "profile-name": "corp-aaa",
                        "dot1x_auth_profile": "corp-dot1x",
                        "dot1x_server_group": "corp-sg",
                    }
                ]
            },
            "/v1/configuration/object/dot1x_auth_profile": {
                "dot1x_auth_profile": [
                    {"profile-name": "corp-dot1x", "reauthentication": True}
                ]
            },
            "/v1/configuration/object/mac_auth_profile": {
                "mac_auth_profile": [{"profile-name": "corp-mac"}]
            },
            "/v1/configuration/object/server_group_prof": {
                "server_group_prof": [
                    {"sg_name": "corp-sg", "auth_server": ["rad1"]}
                ]
            },
            "/v1/configuration/object/rad_server": {
                "rad_server": [{"rad_server_name": "rad1", "rad_host": "10.0.0.10"}]
            },
            "/v1/configuration/object/ldap_server": {
                "ldap_server": [{"ldap_server_name": "ldap1", "ldap_host": "10.0.0.11"}]
            },
            "/v1/configuration/object/tacacs_server": {
                "tacacs_server": [
                    {"tacacs_server_name": "tac1", "tacacs_host": "10.0.0.12"}
                ]
            },
            "/v1/configuration/object/ip_route": {
                "ip_route": [
                    {
                        "destip": "10.20.0.0",
                        "destmask": "255.255.0.0",
                        "nexthop": "10.0.0.254",
                        "zero": 0,
                    }
                ]
            },
            "/v1/configuration/object/ipv6_route": {
                "ipv6_route": [
                    {
                        "destip": "2001:db8:20::/64",
                        "nexthop": "2001:db8::1",
                        "nexthop1": "2001:db8::2",
                        "vlanid": 20,
                        "zero": 0,
                    }
                ]
            },
            "/v1/configuration/object/vrrp": {
                "vrrp": [{"id": 20, "vrrp_ip": "10.0.20.1", "vrrp_vlan": 20}]
            },
            "/v1/configuration/object/vrrp6": {"vrrp6": []},
        }
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_export_all(config_path="/md/lab"))

    assert out["config_path"] == "/md/lab"
    assert out["wlans"]["ssid_profiles"] == [{"profile-name": "Corp", "essid": "Corp"}]
    assert out["roles"] == [{"role": "employee", "vlan": 20}]
    assert out["vlans"] == [{"id": 20, "description": "Corp"}]
    assert out["ap_groups"] == [{"profile-name": "Lab-AP-Group"}]
    assert out["policies"] == [{"name": "corp-acl"}]
    assert out["aaa"]["aaa_profiles"][0]["profile-name"] == "corp-aaa"
    assert out["aaa"]["radius_servers"][0]["rad_server_name"] == "rad1"
    assert out["routing"]["ipv4_routes"][0]["destip"] == "10.20.0.0"
    assert out["routing"]["vrrp"][0]["id"] == 20
    assert out["warnings"] == []


def test_aos8_export_all_warns_on_malformed_success_collection(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {
            "/v1/configuration/object/ssid_prof": {"ssid_prof": []},
            "/v1/configuration/object/virtual_ap": {"virtual_ap": []},
            "/v1/configuration/object/role": {"role": {"unexpected": "object"}},
        }
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_export_all(config_path="/md/lab"))

    assert out["roles"] == []
    assert any(
        "user_roles: response collection was missing or malformed" in warning
        for warning in out["warnings"]
    )


def test_aos8_export_all_reports_warnings_without_aborting(monkeypatch):
    # Only ssid_prof/virtual_ap succeed; every other object type 404s.
    fake_cls = _fake_client_for_paths(
        {
            "/v1/configuration/object/ssid_prof": {"ssid_prof": [{"profile-name": "Corp"}]},
            "/v1/configuration/object/virtual_ap": {"virtual_ap": []},
        }
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    out = asyncio.run(aos8.aos8_export_all(config_path="/md/lab"))

    assert out["roles"] == []
    assert out["vlans"] == []
    assert out["ap_groups"] == []
    assert out["controllers"] == []
    assert out["policies"] == []
    assert len(out["warnings"]) >= 4


def test_aos8_migration_plan_builds_deterministic_plan_from_live_export(monkeypatch):
    fake_cls = _fake_client_for_paths(
        {
            "/v1/configuration/object/ssid_prof": {
                "ssid_prof": [{"profile-name": "Corp", "essid": "Corp", "opmode": "wpa2-aes"}]
            },
            "/v1/configuration/object/virtual_ap": {
                "virtual_ap": [
                    {
                        "profile-name": "Corp-VAP",
                        "ssid-profile": "Corp",
                        "vlan": 20,
                        "aaa-profile": "dot1x",
                        "forward-mode": "tunnel",
                    }
                ]
            },
            "/v1/configuration/object/role": {
                "role": [{"role": "employee", "vlan": 20, "acl": "allowall"}]
            },
            "/v1/configuration/object/vlan_id": {"vlan_id": [{"id": 20, "description": "Corp"}]},
            "/v1/configuration/object/ap_group": {"ap_group": []},
            "/v1/configuration/showcommand": {"Switches": []},
            "/v1/configuration/object/acl_sess": {"acl_sess": []},
        }
    )
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", fake_cls)

    plan = asyncio.run(aos8.aos8_migration_plan(config_path="/md/lab"))

    assert plan["config_path"] == "/md/lab"
    classic_types = {c["object_type"] for c in plan["candidates"]["classic_central"]}
    assert "wlan" in classic_types
    assert "role" in classic_types
    assert any("opmode" in w for w in plan["warnings"])
    assert "verification_plan" in plan
