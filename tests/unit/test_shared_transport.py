from __future__ import annotations

from types import SimpleNamespace

from mcp.server.transport_security import TransportSecuritySettings

from mcp_servers.shared import _configure_http_transport, run_server


class _DummyMCP:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            host="127.0.0.1",
            port=8010,
            transport_security=TransportSecuritySettings(
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=["http://127.0.0.1:*"],
            ),
        )
        self.run_calls: list[dict] = []

    def run(self, **kwargs):
        self.run_calls.append(kwargs)


def test_run_server_configures_http_settings_without_host_port_kwargs(monkeypatch):
    server = _DummyMCP()
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")

    run_server(server)

    assert server.settings.host == "0.0.0.0"
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
