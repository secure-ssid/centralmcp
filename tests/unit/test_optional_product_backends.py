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
