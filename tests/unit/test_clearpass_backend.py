from __future__ import annotations

import asyncio

import mcp_servers.clearpass as clearpass


def test_clearpass_status_unconfigured(monkeypatch):
    monkeypatch.delenv("CLEARPASS_BASE_URL", raising=False)
    monkeypatch.delenv("CLEARPASS_API_TOKEN", raising=False)
    out = clearpass.clearpass_status()
    assert out["configured"] is False
    assert out["has_token"] is False


def test_clearpass_get_rejects_non_api_path(monkeypatch):
    monkeypatch.setenv("CLEARPASS_BASE_URL", "https://cp.example.com")
    monkeypatch.setenv("CLEARPASS_API_TOKEN", "secret")
    out = asyncio.run(clearpass.clearpass_get("/bad/path"))
    assert "error" in out
    assert "/api/*" in out["error"]


def test_clearpass_get_rejects_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("CLEARPASS_BASE_URL", "https://cp.example.com")
    monkeypatch.setenv("CLEARPASS_API_TOKEN", "secret")
    out = asyncio.run(clearpass.clearpass_get("/api/../admin"))
    assert "error" in out
    assert "dot segments" in out["error"]


def test_clearpass_get_rejects_double_encoded_dot_segment_bypass(monkeypatch):
    monkeypatch.setenv("CLEARPASS_BASE_URL", "https://cp.example.com")
    monkeypatch.setenv("CLEARPASS_API_TOKEN", "secret")
    out = asyncio.run(clearpass.clearpass_get("/api/%252e%252e/admin"))
    assert "error" in out
    assert "double-encoded" in out["error"]


def test_clearpass_get_calls_httpx(monkeypatch):
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

    monkeypatch.setenv("CLEARPASS_BASE_URL", "https://cp.example.com")
    monkeypatch.setenv("CLEARPASS_API_TOKEN", "secret")
    monkeypatch.setattr(clearpass.httpx, "AsyncClient", _FakeAsyncClient)

    out = asyncio.run(clearpass.clearpass_get("/api/session", {"limit": 1}))
    assert out["status_code"] == 200
    assert out["data"] == {"ok": True}
    assert called["url"] == "https://cp.example.com/api/session"
