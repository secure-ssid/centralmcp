from __future__ import annotations

import asyncio

import pytest

import mcp_servers.aos8 as aos8
import mcp_servers.apstra as apstra
import mcp_servers.edgeconnect as edgeconnect


class _Resp:
    status_code = 200
    text = '{"ok":true}'

    def json(self):
        return {"ok": True}


class _ListResp:
    status_code = 200
    text = '[{"id":1},{"id":2},{"id":3}]'

    def json(self):
        return [{"id": 1}, {"id": 2}, {"id": 3}]


@pytest.mark.parametrize(
    ("module", "status_func", "env_base", "env_token"),
    [
        (apstra, apstra.apstra_status, "APSTRA_BASE_URL", "APSTRA_API_TOKEN"),
        (aos8, aos8.aos8_status, "AOS8_BASE_URL", "AOS8_API_TOKEN"),
        (
            edgeconnect,
            edgeconnect.edgeconnect_status,
            "EDGECONNECT_BASE_URL",
            "EDGECONNECT_API_TOKEN",
        ),
    ],
)
def test_optional_product_status_unconfigured(
    module,
    status_func,
    env_base,
    env_token,
    monkeypatch,
):
    monkeypatch.delenv(env_base, raising=False)
    monkeypatch.delenv(env_token, raising=False)

    out = status_func()

    assert out["configured"] is False
    assert out["has_token"] is False


def test_apstra_get_rejects_non_api_path(monkeypatch):
    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")

    out = asyncio.run(apstra.apstra_get("/bad/path"))

    assert "error" in out
    assert "/api/*" in out["error"]


def test_apstra_get_rejects_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")

    out = asyncio.run(apstra.apstra_get("/api/../admin"))

    assert "error" in out
    assert "dot segments" in out["error"]


def test_apstra_get_rejects_double_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")

    out = asyncio.run(apstra.apstra_get("/api/%252e%252e/admin"))

    assert "error" in out
    assert "double-encoded" in out["error"]


def test_apstra_get_calls_httpx(monkeypatch):
    called = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            called["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["headers"] = headers or {}
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_get("/api/blueprints", {"limit": 1}))

    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["url"] == "https://apstra.example.com/api/blueprints"
    assert called["headers"]["Authorization"] == "Bearer secret"


def test_apstra_get_bounds_list_payloads(monkeypatch):
    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            return _ListResp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_get("/api/blueprints", limit=2, offset=1))

    assert out["data"] == {
        "items": [{"id": 2}, {"id": 3}],
        "_pagination": {
            "offset": 1,
            "limit": 2,
            "total": 3,
            "truncated": False,
        },
    }


def test_apstra_list_blueprints_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '[{"id":"bp1"}]'

        def json(self):
            return [
                {
                    "id": "bp1",
                    "label": "DC1",
                    "status": "ready",
                    "raw": "omitted",
                }
            ]

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_blueprints(limit=10))

    assert called["url"] == "https://apstra.example.com/api/blueprints"
    assert out["blueprints"]["items"] == [
        {"id": "bp1", "label": "DC1", "status": "ready"}
    ]


def test_apstra_list_templates_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"items":[{"id":"tmpl1"}]}'

        def json(self):
            return {
                "items": [
                    {
                        "id": "tmpl1",
                        "label": "5-stage Clos",
                        "design": "datacenter",
                        "version": "1.0",
                        "raw": "omitted",
                    },
                    {
                        "id": "tmpl2",
                        "label": "Collapsed core",
                        "design": "datacenter",
                        "version": "1.0",
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_templates(limit=1))

    assert called["url"] == "https://apstra.example.com/api/design/templates"
    assert out["templates"]["items"] == [
        {"id": "tmpl1", "label": "5-stage Clos", "design": "datacenter", "version": "1.0"}
    ]
    assert out["templates"]["_pagination"]["truncated"] is True


def test_apstra_list_anomalies_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"items":[{"id":"a1"}]}'

        def json(self):
            return {
                "items": [
                    {
                        "id": "a1",
                        "type": "bgp",
                        "severity": "critical",
                        "details": "omitted",
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_anomalies("bp 1"))

    assert called["url"] == "https://apstra.example.com/api/blueprints/bp%201/anomalies"
    assert out["blueprint_id"] == "bp 1"
    assert out["anomalies"]["items"] == [
        {"id": "a1", "type": "bgp", "severity": "critical"}
    ]


def test_apstra_list_racks_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"items":[{"id":"rack1"}]}'

        def json(self):
            return {
                "items": [
                    {
                        "id": "rack1",
                        "label": "Rack 1",
                        "rack_type": "rack_based",
                        "leaf_count": 2,
                        "spine_count": 0,
                        "raw": "omitted",
                    },
                    {
                        "id": "rack2",
                        "label": "Rack 2",
                        "rack_type": "rack_based",
                        "leaf_count": 2,
                        "spine_count": 0,
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_racks("bp 1", limit=1))

    assert called["url"] == "https://apstra.example.com/api/blueprints/bp%201/racks"
    assert out["blueprint_id"] == "bp 1"
    assert out["racks"]["items"] == [
        {
            "id": "rack1",
            "label": "Rack 1",
            "rack_type": "rack_based",
            "leaf_count": 2,
            "spine_count": 0,
        }
    ]
    assert out["racks"]["_pagination"]["truncated"] is True


def test_apstra_list_routing_zones_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"items":[{"id":"sz1"}]}'

        def json(self):
            return {
                "items": [
                    {
                        "id": "sz1",
                        "label": "default",
                        "vni": 10001,
                        "vrf_name": "default",
                        "raw": "omitted",
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_routing_zones("bp 1"))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/security-zones"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["routing_zones"]["items"] == [
        {"id": "sz1", "label": "default", "vni": 10001, "vrf_name": "default"}
    ]


def test_apstra_list_virtual_networks_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"items":[{"id":"vn1"}]}'

        def json(self):
            return {
                "items": [
                    {
                        "id": "vn1",
                        "label": "App",
                        "vn_type": "vxlan",
                        "security_zone_id": "sz1",
                        "virtual_gateway_ipv4": "10.10.1.1",
                        "ipv4_subnet": "10.10.1.0/24",
                        "bound_to": [{"system_id": "leaf1", "vlan_id": 101}],
                        "raw": "omitted",
                    },
                    {
                        "id": "vn2",
                        "label": "Db",
                        "vn_type": "vxlan",
                        "security_zone_id": "sz1",
                        "virtual_gateway_ipv4": "10.10.2.1",
                        "ipv4_subnet": "10.10.2.0/24",
                        "bound_to": [{"system_id": "leaf2", "vlan_id": 102}],
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_virtual_networks("bp 1", limit=1))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/virtual-networks"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["virtual_networks"]["items"] == [
        {
            "id": "vn1",
            "label": "App",
            "vn_type": "vxlan",
            "security_zone_id": "sz1",
            "virtual_gateway_ipv4": "10.10.1.1",
            "ipv4_subnet": "10.10.1.0/24",
            "bound_to": [{"system_id": "leaf1", "vlan_id": 101}],
        }
    ]
    assert out["virtual_networks"]["_pagination"]["truncated"] is True


def test_apstra_list_remote_gateways_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"remote_gateways":[{"id":"gw1"}]}'

        def json(self):
            return {
                "remote_gateways": [
                    {
                        "id": "gw1",
                        "gw_name": "remote-a",
                        "gw_ip": "198.51.100.1",
                        "gw_asn": 65001,
                        "evpn_route_types": "all",
                        "local_gw_nodes": ["leaf1"],
                        "raw": "omitted",
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_remote_gateways("bp 1"))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/remote_gateways"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["remote_gateways"]["remote_gateways"] == [
        {
            "id": "gw1",
            "gw_name": "remote-a",
            "gw_ip": "198.51.100.1",
            "gw_asn": 65001,
            "local_gw_nodes": ["leaf1"],
            "evpn_route_types": "all",
        }
    ]


def test_apstra_list_connectivity_templates_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"policies":[{"id":"ct1"}]}'

        def json(self):
            return {
                "policies": [
                    {
                        "id": "ct1",
                        "label": "Access VLAN",
                        "policy_type": "batch",
                        "visible": True,
                        "used": False,
                        "raw": "omitted",
                    },
                    {
                        "id": "ct2",
                        "label": "Trunk",
                        "policy_type": "batch",
                        "visible": True,
                        "used": True,
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_connectivity_templates("bp 1", limit=1))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/obj-policy-export"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["connectivity_templates"]["policies"] == [
        {
            "id": "ct1",
            "label": "Access VLAN",
            "policy_type": "batch",
            "visible": True,
            "used": False,
        }
    ]
    assert out["connectivity_templates"]["_pagination"]["truncated"] is True


def test_apstra_list_application_endpoints_uses_read_only_post_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"application_points":[{"id":"if1"}]}'

        def json(self):
            return {
                "application_points": [
                    {
                        "id": "if1",
                        "label": "leaf1:xe-0/0/1",
                        "system_id": "leaf1",
                        "interface_id": "xe-0/0/1",
                        "interface_name": "xe-0/0/1",
                        "assigned": False,
                        "raw": "omitted",
                    },
                    {
                        "id": "if2",
                        "label": "leaf1:xe-0/0/2",
                        "system_id": "leaf1",
                        "interface_id": "xe-0/0/2",
                        "interface_name": "xe-0/0/2",
                        "assigned": True,
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers=None):
            called["url"] = url
            called["headers"] = headers or {}
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_application_endpoints("bp 1", limit=1))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/obj-policy-application-points"
    )
    assert "Authorization" in called["headers"]
    assert out["blueprint_id"] == "bp 1"
    assert out["application_endpoints"]["application_points"] == [
        {
            "id": "if1",
            "label": "leaf1:xe-0/0/1",
            "system_id": "leaf1",
            "interface_id": "xe-0/0/1",
            "interface_name": "xe-0/0/1",
            "assigned": False,
        }
    ]
    assert out["application_endpoints"]["_pagination"]["truncated"] is True


def test_apstra_get_diff_status_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"status":"staged"}'

        def json(self):
            return {
                "status": "staged",
                "staging_version": 7,
                "active_version": 6,
                "has_uncommitted_changes": True,
                "raw": "omitted",
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_get_diff_status("bp 1"))

    assert called["url"] == "https://apstra.example.com/api/blueprints/bp%201/diff-status"
    assert out["blueprint_id"] == "bp 1"
    assert out["diff_status"] == {
        "status": "staged",
        "staging_version": 7,
        "active_version": 6,
        "has_uncommitted_changes": True,
    }


def test_apstra_list_protocol_sessions_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"sessions":[{"id":"bgp1"}]}'

        def json(self):
            return {
                "sessions": [
                    {
                        "id": "bgp1",
                        "protocol": "bgp",
                        "local_system_id": "leaf1",
                        "remote_system_id": "spine1",
                        "state": "Established",
                        "raw": "omitted",
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_list_protocol_sessions("bp 1"))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/protocol-sessions"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["protocol_sessions"]["sessions"] == [
        {
            "id": "bgp1",
            "protocol": "bgp",
            "local_system_id": "leaf1",
            "remote_system_id": "spine1",
            "state": "Established",
        }
    ]


def test_apstra_get_system_info_quotes_blueprint_id_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"systems":[{"id":"sys1"}]}'

        def json(self):
            return {
                "systems": [
                    {
                        "id": "sys1",
                        "label": "leaf-1",
                        "role": "leaf",
                        "status": "ready",
                        "management_ip": "192.0.2.10",
                        "raw": "omitted",
                    },
                    {
                        "id": "sys2",
                        "label": "spine-1",
                        "role": "spine",
                        "status": "ready",
                        "management_ip": "192.0.2.11",
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("APSTRA_BASE_URL", "https://apstra.example.com")
    monkeypatch.setenv("APSTRA_API_TOKEN", "secret")
    monkeypatch.setattr(apstra.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(apstra.apstra_get_system_info("bp 1", limit=1))

    assert called["url"] == (
        "https://apstra.example.com/api/blueprints/bp%201/experience/web/system-info"
    )
    assert out["blueprint_id"] == "bp 1"
    assert out["systems"]["systems"] == [
        {
            "id": "sys1",
            "label": "leaf-1",
            "role": "leaf",
            "status": "ready",
            "management_ip": "192.0.2.10",
        }
    ]
    assert out["systems"]["_pagination"]["truncated"] is True


def test_aos8_get_rejects_non_v1_path(monkeypatch):
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")

    out = asyncio.run(aos8.aos8_get("/api/bad"))

    assert "error" in out
    assert "/v1/*" in out["error"]


def test_aos8_get_rejects_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")

    out = asyncio.run(aos8.aos8_get("/v1/../admin"))

    assert "error" in out
    assert "dot segments" in out["error"]


def test_aos8_get_rejects_double_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")

    out = asyncio.run(aos8.aos8_get("/v1/%252e%252e/admin"))

    assert "error" in out
    assert "double-encoded" in out["error"]


def test_aos8_get_calls_httpx(monkeypatch):
    called = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            called["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["headers"] = headers or {}
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(aos8.aos8_get("/v1/configuration/object", {"limit": 1}))

    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["url"] == "https://mm.example.com/v1/configuration/object"
    assert called["headers"]["Authorization"] == "Bearer secret"


def test_aos8_show_command_rejects_non_show(monkeypatch):
    out = asyncio.run(aos8.aos8_show_command("write memory"))

    assert "error" in out
    assert "Only 'show' commands" in out["error"]


def test_aos8_show_command_calls_showcommand_and_strips_envelope(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"rows":[{"name":"mc1"}]}'

        def json(self):
            return {
                "_global_result": {"status": "0"},
                "_meta": {"rows": ["name"]},
                "rows": [{"name": "mc1"}, {"name": "mc2"}, {"name": "mc3"}],
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        aos8.aos8_show_command(" show switchinfo", config_path="/md/branch1", limit=2, offset=1)
    )

    assert called["url"] == "https://mm.example.com/v1/configuration/showcommand"
    assert called["params"] == {"command": "show switchinfo", "config_path": "/md/branch1"}
    assert out["command"] == "show switchinfo"
    assert "_global_result" not in out["data"]
    assert "_meta" not in out["data"]
    assert out["data"]["rows"] == [{"name": "mc2"}, {"name": "mc3"}]


def test_aos8_list_aps_runs_show_ap_database_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"AP Database":[{"Name":"ap1"}]}'

        def json(self):
            return {
                "_global_result": {"status": "0"},
                "_meta": {"AP Database": ["Name"]},
                "AP Database": [
                    {
                        "Name": "ap1",
                        "Group": "HQ",
                        "IP Address": "192.0.2.10",
                        "Status": "Up",
                        "Raw": "omitted",
                    },
                    {
                        "Name": "ap2",
                        "Group": "HQ",
                        "IP Address": "192.0.2.11",
                        "Status": "Down",
                        "Raw": "omitted",
                    },
                ],
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(aos8.aos8_list_aps(config_path="/md/branch1", limit=1))

    assert called["url"] == "https://mm.example.com/v1/configuration/showcommand"
    assert called["params"] == {
        "command": "show ap database",
        "config_path": "/md/branch1",
    }
    assert out["config_path"] == "/md/branch1"
    assert out["aps"]["AP Database"] == [
        {"Name": "ap1", "Group": "HQ", "IP Address": "192.0.2.10", "Status": "Up"}
    ]
    assert out["aps"]["_pagination"]["truncated"] is True


def test_aos8_list_clients_runs_show_user_table_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"Users":[{"Name":"alice"}]}'

        def json(self):
            return {
                "_global_result": {"status": "0"},
                "_meta": {"Users": ["Name"]},
                "Users": [
                    {
                        "Name": "alice",
                        "MAC Address": "aa:bb:cc:dd:ee:01",
                        "IP Address": "192.0.2.50",
                        "AP Name": "ap1",
                        "SSID": "Corp",
                        "Role": "employee",
                        "VLAN": 20,
                        "Raw": "omitted",
                    },
                    {
                        "Name": "bob",
                        "MAC Address": "aa:bb:cc:dd:ee:02",
                        "IP Address": "192.0.2.51",
                        "AP Name": "ap2",
                        "SSID": "Corp",
                        "Role": "employee",
                        "VLAN": 20,
                        "Raw": "omitted",
                    },
                ],
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(aos8.aos8_list_clients(config_path="/md/branch1", limit=1))

    assert called["url"] == "https://mm.example.com/v1/configuration/showcommand"
    assert called["params"] == {
        "command": "show user-table",
        "config_path": "/md/branch1",
    }
    assert out["config_path"] == "/md/branch1"
    assert out["clients"]["Users"] == [
        {
            "Name": "alice",
            "MAC Address": "aa:bb:cc:dd:ee:01",
            "IP Address": "192.0.2.50",
            "AP Name": "ap1",
            "SSID": "Corp",
            "Role": "employee",
            "VLAN": 20,
        }
    ]
    assert out["clients"]["_pagination"]["truncated"] is True


def test_aos8_list_ssid_profiles_uses_config_object(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"ssid_prof":[{"profile-name":"Corp"}]}'

        def json(self):
            return {
                "_global_result": {"status": "0"},
                "_meta": {"ssid_prof": ["profile-name"]},
                "ssid_prof": [{"profile-name": "Corp", "opmode": "wpa2-aes"}],
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("AOS8_BASE_URL", "https://mm.example.com")
    monkeypatch.setenv("AOS8_API_TOKEN", "secret")
    monkeypatch.setattr(aos8.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(aos8.aos8_list_ssid_profiles(config_path="/md/branch1"))

    assert called["url"] == "https://mm.example.com/v1/configuration/object/ssid_prof"
    assert called["params"] == {"config_path": "/md/branch1"}
    assert out["config_path"] == "/md/branch1"
    assert out["ssid_profiles"]["ssid_prof"] == [
        {"profile-name": "Corp", "opmode": "wpa2-aes"}
    ]


def test_edgeconnect_get_rejects_unknown_path(monkeypatch):
    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")

    out = asyncio.run(edgeconnect.edgeconnect_get("/api/bad"))

    assert "error" in out
    assert "/gms/rest/*" in out["error"]


def test_edgeconnect_get_rejects_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")

    out = asyncio.run(edgeconnect.edgeconnect_get("/gms/rest/../admin"))

    assert "error" in out
    assert "dot segments" in out["error"]


def test_edgeconnect_get_rejects_double_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")

    out = asyncio.run(edgeconnect.edgeconnect_get("/gms/rest/%252e%252e/admin"))

    assert "error" in out
    assert "double-encoded" in out["error"]


def test_edgeconnect_get_calls_httpx_with_custom_auth_header(monkeypatch):
    called = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            called["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["headers"] = headers or {}
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setenv("EDGECONNECT_AUTH_HEADER", "X-Auth-Token")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_get("/gms/rest/appliance", {"limit": 1}))

    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["url"] == "https://orch.example.com/gms/rest/appliance"
    assert called["headers"]["X-Auth-Token"] == "secret"


def test_edgeconnect_list_appliances_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '[{"nePk":"1"}]'

        def json(self):
            return [
                {
                    "nePk": "1",
                    "hostName": "ec-1",
                    "model": "EC-V",
                    "status": "normal",
                    "raw": "omitted",
                }
            ]

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_list_appliances(limit=10))

    assert called["url"] == "https://orch.example.com/gms/rest/appliance"
    assert out["appliances"]["items"] == [
        {"nePk": "1", "hostName": "ec-1", "model": "EC-V", "status": "normal"}
    ]


def test_edgeconnect_get_system_info_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"hostName":"ec-1"}'

        def json(self):
            return {
                "hostName": "ec-1",
                "modelShort": "EC-V",
                "status": "Normal",
                "release": "ECOS 9.5.2.1",
                "alarmSummary": {"num_outstanding": 0},
                "raw": "omitted",
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://ec.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_get_system_info())

    assert called["url"] == "https://ec.example.com/rest/json/systemInfo"
    assert out["system_info"] == {
        "hostName": "ec-1",
        "modelShort": "EC-V",
        "status": "Normal",
        "release": "ECOS 9.5.2.1",
        "alarmSummary": {"num_outstanding": 0},
    }


def test_edgeconnect_list_alarms_compacts_outstanding(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"outstanding":[{"id":"alarm1"}]}'

        def json(self):
            return {
                "outstanding": [
                    {
                        "id": "alarm1",
                        "severity": "critical",
                        "message": "Link down",
                        "raw": "omitted",
                    },
                    {
                        "id": "alarm2",
                        "severity": "minor",
                        "message": "Peer changed",
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://ec.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_list_alarms(limit=1))

    assert called["url"] == "https://ec.example.com/rest/json/alarm"
    assert out["alarms"]["outstanding"] == [
        {"id": "alarm1", "severity": "critical", "message": "Link down"}
    ]
    assert out["alarms"]["_pagination"]["truncated"] is True


def test_edgeconnect_list_tunnels_filters_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"tunnels":[{"id":"tun1"}]}'

        def json(self):
            return {
                "tunnels": [
                    {
                        "id": "tun1",
                        "alias": "branch-a",
                        "srcNePk": "1.NE",
                        "destNePk": "2.NE",
                        "operStatus": "Up",
                        "adminStatus": "Up",
                        "raw": "omitted",
                    },
                    {
                        "id": "tun2",
                        "alias": "branch-b",
                        "srcNePk": "1.NE",
                        "destNePk": "3.NE",
                        "operStatus": "Down",
                        "adminStatus": "Up",
                        "raw": "omitted",
                    },
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        edgeconnect.edgeconnect_list_tunnels(
            ne_pk="1.NE",
            state="up|down",
            matching_alias="branch",
            limit=1,
        )
    )

    assert called["url"] == "https://orch.example.com/gms/rest/tunnels2/physical"
    assert called["params"] == {
        "nePk": "1.NE",
        "state": "up|down",
        "matchingAlias": "branch",
        "limit": 1,
    }
    assert out["tunnels"]["tunnels"] == [
        {
            "id": "tun1",
            "alias": "branch-a",
            "srcNePk": "1.NE",
            "destNePk": "2.NE",
            "operStatus": "Up",
            "adminStatus": "Up",
        }
    ]
    assert out["tunnels"]["_pagination"]["truncated"] is True


def test_edgeconnect_get_tunnel_metadata_sets_metadata_param(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"totalTunnels":12}'

        def json(self):
            return {
                "totalTunnels": 12,
                "physicalTunnels": 8,
                "bondedTunnels": 4,
                "raw": "omitted",
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_get_tunnel_metadata())

    assert called["url"] == "https://orch.example.com/gms/rest/tunnels2"
    assert called["params"] == {"metaData": True}
    assert out["tunnel_metadata"] == {
        "totalTunnels": 12,
        "physicalTunnels": 8,
        "bondedTunnels": 4,
    }


def test_edgeconnect_list_vrf_segments_filters_and_compacts(monkeypatch):
    called = {}

    class _Resp:
        status_code = 200
        text = '{"1":{"name":"Corp"}}'

        def json(self):
            return {
                "0": {
                    "name": "Default",
                    "vrfName": "default",
                    "enabled": True,
                    "status": "active",
                    "raw": "omitted",
                },
                "1": {
                    "name": "Corp",
                    "vrfName": "corp-vrf",
                    "enabled": True,
                    "status": "active",
                    "raw": "omitted",
                },
                "metadata": {"raw": "not a segment"},
            }

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None, params=None):
            called["url"] = url
            called["params"] = params or {}
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(edgeconnect.edgeconnect_list_vrf_segments(segment_id=1, limit=1))

    assert called["url"] == "https://orch.example.com/gms/rest/vrf/config/segments"
    assert called["params"] == {"id": 1}
    assert out["vrf_segments"]["items"] == [
        {
            "id": 1,
            "name": "Corp",
            "vrfName": "corp-vrf",
            "enabled": True,
            "status": "active",
        }
    ]
    assert out["vrf_segments"]["_pagination"]["total"] == 1
    assert out["vrf_segments"]["_pagination"]["truncated"] is False


@pytest.mark.parametrize(
    ("write_func", "env_base", "env_token", "base_url", "path", "expected_url"),
    [
        (
            apstra.apstra_write,
            "APSTRA_BASE_URL",
            "APSTRA_API_TOKEN",
            "https://apstra.example.com",
            "/api/blueprints/bp1",
            "https://apstra.example.com/api/blueprints/bp1",
        ),
        (
            aos8.aos8_write,
            "AOS8_BASE_URL",
            "AOS8_API_TOKEN",
            "https://mm.example.com",
            "/v1/configuration/object",
            "https://mm.example.com/v1/configuration/object",
        ),
        (
            edgeconnect.edgeconnect_write,
            "EDGECONNECT_BASE_URL",
            "EDGECONNECT_API_TOKEN",
            "https://orch.example.com",
            "/gms/rest/appliance",
            "https://orch.example.com/gms/rest/appliance",
        ),
    ],
)
def test_optional_product_write_dry_run_previews(
    write_func,
    env_base,
    env_token,
    base_url,
    path,
    expected_url,
    monkeypatch,
):
    monkeypatch.setenv(env_base, base_url)
    monkeypatch.setenv(env_token, "secret")

    out = asyncio.run(
        write_func(
            "patch",
            path,
            params={"api_key": "abc", "reason": "lab"},
            body={"password": "secret", "enabled": True},
        )
    )

    assert out["dry_run"] is True
    assert out["method"] == "PATCH"
    assert out["url"] == expected_url
    assert out["params"] == {"api_key": "******", "reason": "lab"}
    assert out["json"] == {"password": "******", "enabled": True}
    assert "execute_hint" in out


@pytest.mark.parametrize(
    ("write_func", "path"),
    [
        (apstra.apstra_write, "/api/blueprints/bp1"),
        (aos8.aos8_write, "/v1/configuration/object"),
        (edgeconnect.edgeconnect_write, "/gms/rest/appliance"),
    ],
)
def test_optional_product_write_blocks_when_product_access_read_only(
    write_func,
    path,
    monkeypatch,
):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")

    out = asyncio.run(write_func("patch", path, body={"enabled": True}))

    assert out["status"] == "blocked"
    assert "CENTRALMCP_PRODUCT_ACCESS=read-only" in out["error"]


@pytest.mark.parametrize(
    ("module", "write_func", "env_base", "env_token", "base_url", "path"),
    [
        (
            apstra,
            apstra.apstra_write,
            "APSTRA_BASE_URL",
            "APSTRA_API_TOKEN",
            "https://apstra.example.com",
            "/api/blueprints/bp1",
        ),
        (
            aos8,
            aos8.aos8_write,
            "AOS8_BASE_URL",
            "AOS8_API_TOKEN",
            "https://mm.example.com",
            "/v1/configuration/object",
        ),
        (
            edgeconnect,
            edgeconnect.edgeconnect_write,
            "EDGECONNECT_BASE_URL",
            "EDGECONNECT_API_TOKEN",
            "https://orch.example.com",
            "/gms/rest/appliance",
        ),
    ],
)
def test_optional_product_write_requires_confirm_when_not_dry_run(
    module,
    write_func,
    env_base,
    env_token,
    base_url,
    path,
    monkeypatch,
):
    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, params=None, json=None):
            raise AssertionError("request should not execute without confirm=True")

    monkeypatch.setenv(env_base, base_url)
    monkeypatch.setenv(env_token, "secret")
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        write_func("PATCH", path, body={"name": "lab"}, dry_run=False, confirm=False)
    )

    assert out["dry_run"] is True
    assert out["error"] == "confirm=True is required when dry_run=False."


@pytest.mark.parametrize(
    ("module", "write_func", "env_base", "env_token", "base_url", "path", "expected_url"),
    [
        (
            apstra,
            apstra.apstra_write,
            "APSTRA_BASE_URL",
            "APSTRA_API_TOKEN",
            "https://apstra.example.com",
            "/api/blueprints/bp1",
            "https://apstra.example.com/api/blueprints/bp1",
        ),
        (
            aos8,
            aos8.aos8_write,
            "AOS8_BASE_URL",
            "AOS8_API_TOKEN",
            "https://mm.example.com",
            "/v1/configuration/object",
            "https://mm.example.com/v1/configuration/object",
        ),
        (
            edgeconnect,
            edgeconnect.edgeconnect_write,
            "EDGECONNECT_BASE_URL",
            "EDGECONNECT_API_TOKEN",
            "https://orch.example.com",
            "/gms/rest/appliance",
            "https://orch.example.com/gms/rest/appliance",
        ),
    ],
)
def test_optional_product_write_executes_with_default_bearer_auth(
    module,
    write_func,
    env_base,
    env_token,
    base_url,
    path,
    expected_url,
    monkeypatch,
):
    called = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, params=None, json=None):
            called["method"] = method
            called["url"] = url
            called["headers"] = headers or {}
            called["params"] = params or {}
            called["json"] = json
            return _Resp()

    monkeypatch.setenv(env_base, base_url)
    monkeypatch.setenv(env_token, "secret")
    monkeypatch.delenv("EDGECONNECT_AUTH_HEADER", raising=False)
    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        write_func("PATCH", path, body={"name": "lab"}, dry_run=False, confirm=True)
    )

    assert out["status_code"] == 200
    assert called["method"] == "PATCH"
    assert called["url"] == expected_url
    assert called["headers"]["Authorization"] == "Bearer secret"
    assert called["json"] == {"name": "lab"}


def test_edgeconnect_write_executes_with_custom_auth_header(monkeypatch):
    called = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, params=None, json=None):
            called["method"] = method
            called["url"] = url
            called["headers"] = headers or {}
            called["params"] = params or {}
            called["json"] = json
            return _Resp()

    monkeypatch.setenv("EDGECONNECT_BASE_URL", "https://orch.example.com")
    monkeypatch.setenv("EDGECONNECT_API_TOKEN", "secret")
    monkeypatch.setenv("EDGECONNECT_AUTH_HEADER", "X-Auth-Token")
    monkeypatch.setattr(edgeconnect.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(
        edgeconnect.edgeconnect_write(
            "POST",
            "/gms/rest/appliance",
            body={"name": "lab"},
            dry_run=False,
            confirm=True,
        )
    )

    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["method"] == "POST"
    assert called["url"] == "https://orch.example.com/gms/rest/appliance"
    assert called["headers"]["X-Auth-Token"] == "secret"
    assert called["json"] == {"name": "lab"}
