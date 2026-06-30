from __future__ import annotations

import asyncio

import mcp_servers.mist as mist


def test_mist_status_unconfigured(monkeypatch):
    monkeypatch.delenv("MIST_API_TOKEN", raising=False)
    out = mist.mist_status()
    assert out["configured"] is False
    assert out["host"] == "https://api.mist.com"
    assert out["has_token"] is False


def test_mist_get_rejects_non_api_v1_path(monkeypatch):
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    out = asyncio.run(mist.mist_get("/bad/path"))
    assert "error" in out
    assert "/api/v1/*" in out["error"]


def test_mist_get_rejects_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    out = asyncio.run(mist.mist_get("/api/v1/%2e%2e/admin"))
    assert "error" in out
    assert "encoded dot" in out["error"]


def test_mist_get_rejects_double_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    out = asyncio.run(mist.mist_get("/api/v1/%252e%252e/admin"))
    assert "error" in out
    assert "double-encoded" in out["error"]


def test_mist_get_calls_httpx(monkeypatch):
    class _Resp:
        status_code = 200
        text = '{"ok":true}'

        def json(self):
            return {"ok": True}

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

    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    monkeypatch.setattr(mist.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(mist.mist_get("/api/v1/self", {"limit": 1}))
    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["url"] == "https://api.mist.com/api/v1/self"
    assert called["headers"]["Authorization"] == "Token secret"
