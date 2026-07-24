from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.server.transport_security import TransportSecuritySettings

from mcp_servers.shared import (
    BearerAuthASGIMiddleware,
    UnsafeHttpBindingError,
    _configure_http_transport,
    _http_bearer_token,
    _is_loopback_host,
    _register_health_routes,
    run_server,
)


class _DummyMCP:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self.settings = SimpleNamespace(
            host=host,
            port=8010,
            log_level="INFO",
            transport_security=TransportSecuritySettings(
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=["http://127.0.0.1:*"],
            ),
        )
        self.run_calls: list[dict] = []
        self.custom_routes: list[tuple[str, list[str]]] = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)

    def custom_route(self, path, methods, name=None, include_in_schema=True):
        def decorator(fn):
            self.custom_routes.append((path, methods))
            return fn

        return decorator


def test_run_server_configures_http_settings_without_host_port_kwargs(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "9000")

    run_server(server)

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 9000
    assert server.run_calls == [{"transport": "streamable-http"}]


def test_run_server_defaults_http_to_8010(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)

    run_server(server)

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8010
    assert server.run_calls == [{"transport": "streamable-http"}]


def test_run_server_registers_health_routes_on_http(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")

    run_server(server)

    paths = {path for path, _methods in server.custom_routes}
    assert {"/livez", "/readyz", "/healthz"} <= paths


def test_register_health_routes_is_idempotent():
    server = _DummyMCP()
    _register_health_routes(server)
    _register_health_routes(server)

    assert len(server.custom_routes) == 3


def test_configure_http_transport_applies_security_allowlists(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com,localhost:*")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://app.example.com,http://localhost:*")
    monkeypatch.setenv("MCP_DNS_REBINDING_PROTECTION", "true")

    _configure_http_transport(server, "127.0.0.1", 8010)

    security = server.settings.transport_security
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.example.com", "localhost:*"]
    assert security.allowed_origins == ["https://app.example.com", "http://localhost:*"]


def test_run_server_stdio_keeps_default_run(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")

    run_server(server)

    assert server.run_calls == [{}]


# ---------------------------------------------------------------------------
# Host/origin allow-list hardening for non-loopback MCP_HOST
# ---------------------------------------------------------------------------


class TestLoopbackDetection:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.5.5.5"])
    def test_loopback_hosts(self, host):
        assert _is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "mcp.example.com", "::"])
    def test_non_loopback_hosts(self, host):
        assert _is_loopback_host(host) is False


class TestUnsafeBindingRefusal:
    def test_public_bind_without_allowlist_raises(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

        with pytest.raises(UnsafeHttpBindingError, match="MCP_ALLOWED_HOSTS"):
            _configure_http_transport(server, "0.0.0.0", 8010)

    def test_public_bind_with_partial_allowlist_raises(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com:*")
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

        with pytest.raises(UnsafeHttpBindingError):
            _configure_http_transport(server, "0.0.0.0", 8010)

    def test_public_bind_with_wildcard_allowlist_raises(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "*")

        with pytest.raises(UnsafeHttpBindingError, match="wildcard"):
            _configure_http_transport(server, "0.0.0.0", 8010)

    def test_public_bind_wildcard_allowed_with_explicit_opt_in(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "*")
        monkeypatch.setenv("CENTRALMCP_ALLOW_WILDCARD_HTTP_ALLOWLIST", "1")

        _configure_http_transport(server, "0.0.0.0", 8010)

        assert server.settings.transport_security.allowed_hosts == ["*"]

    def test_public_bind_with_explicit_allowlist_succeeds(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com:*")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")

        _configure_http_transport(server, "0.0.0.0", 8010)

        assert server.settings.transport_security.allowed_hosts == ["mcp.example.com:*"]
        assert server.settings.transport_security.allowed_origins == ["https://mcp.example.com"]

    def test_public_bind_with_dns_rebinding_disabled_raises(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com:*")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")
        monkeypatch.setenv("MCP_DNS_REBINDING_PROTECTION", "0")

        with pytest.raises(UnsafeHttpBindingError, match="DNS-rebinding"):
            _configure_http_transport(server, "0.0.0.0", 8010)

    def test_public_bind_with_dns_rebinding_disabled_allowed_with_opt_in(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com:*")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")
        monkeypatch.setenv("MCP_DNS_REBINDING_PROTECTION", "0")
        monkeypatch.setenv("CENTRALMCP_ALLOW_INSECURE_HTTP_BINDING", "1")

        _configure_http_transport(server, "0.0.0.0", 8010)  # should not raise

    def test_loopback_bind_never_requires_allowlist(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

        _configure_http_transport(server, "127.0.0.1", 8010)  # should not raise

    def test_run_server_propagates_unsafe_binding_error(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        monkeypatch.setenv("MCP_HOST", "0.0.0.0")
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

        with pytest.raises(UnsafeHttpBindingError):
            run_server(server)

        assert server.run_calls == []  # never reached mcp_instance.run()


# ---------------------------------------------------------------------------
# Optional bearer-token protection
# ---------------------------------------------------------------------------


class TestBearerToken:
    def test_bearer_token_unset_by_default(self, monkeypatch):
        monkeypatch.delenv("MCP_HTTP_BEARER_TOKEN", raising=False)
        assert _http_bearer_token() is None

    def test_bearer_token_blank_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", "   ")
        assert _http_bearer_token() is None

    def test_bearer_token_read_from_env(self, monkeypatch):
        monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", "s3cr3t")
        assert _http_bearer_token() == "s3cr3t"

    def test_run_server_without_bearer_token_uses_default_run(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
        monkeypatch.delenv("MCP_HTTP_BEARER_TOKEN", raising=False)

        run_server(server)

        assert server.run_calls == [{"transport": "streamable-http"}]

    def test_run_server_with_bearer_token_on_sse_fails_closed(self, monkeypatch):
        server = _DummyMCP()
        monkeypatch.setenv("MCP_TRANSPORT", "sse")
        monkeypatch.setenv("MCP_HTTP_BEARER_TOKEN", "s3cr3t")

        with pytest.raises(UnsafeHttpBindingError, match="cannot enforce"):
            run_server(server)

        assert server.run_calls == []


class _RecordingASGIApp:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope)
        response_start = {"type": "http.response.start", "status": 200, "headers": []}
        await send(response_start)
        await send({"type": "http.response.body", "body": b"{}"})


async def _drive_asgi(app, path: str, headers: list[tuple[bytes, bytes]]):
    scope = {"type": "http", "path": path, "headers": headers}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


class TestBearerAuthASGIMiddleware:
    def test_health_paths_are_exempt(self):
        import asyncio

        inner = _RecordingASGIApp()
        mw = BearerAuthASGIMiddleware(inner, token="s3cr3t")

        sent = asyncio.run(_drive_asgi(mw, "/livez", []))

        assert inner.calls, "inner app should have been called for an exempt path"
        assert sent[0]["status"] == 200

    def test_missing_authorization_is_rejected(self):
        import asyncio

        inner = _RecordingASGIApp()
        mw = BearerAuthASGIMiddleware(inner, token="s3cr3t")

        sent = asyncio.run(_drive_asgi(mw, "/mcp", []))

        assert not inner.calls, "inner app must not run without a valid token"
        assert sent[0]["status"] == 401

    def test_wrong_token_is_rejected(self):
        import asyncio

        inner = _RecordingASGIApp()
        mw = BearerAuthASGIMiddleware(inner, token="s3cr3t")

        sent = asyncio.run(
            _drive_asgi(mw, "/mcp", [(b"authorization", b"Bearer wrong")])
        )

        assert not inner.calls
        assert sent[0]["status"] == 401

    def test_correct_token_is_accepted(self):
        import asyncio

        inner = _RecordingASGIApp()
        mw = BearerAuthASGIMiddleware(inner, token="s3cr3t")

        sent = asyncio.run(
            _drive_asgi(mw, "/mcp", [(b"authorization", b"Bearer s3cr3t")])
        )

        assert inner.calls
        assert sent[0]["status"] == 200
