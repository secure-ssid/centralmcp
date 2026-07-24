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
    assert out["warnings"] == []


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
