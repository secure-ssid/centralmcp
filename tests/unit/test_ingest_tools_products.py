from __future__ import annotations

from scripts import ingest_tools


def test_server_specs_default_core_only(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)

    specs = ingest_tools._server_specs()

    assert ("aruba-config", "mcp_servers.config") in specs
    assert ("clearpass-core", "mcp_servers.clearpass") not in specs


def test_server_specs_uses_products_argument(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCTS", "clearpass")

    specs = ingest_tools._server_specs("mist,apstra")

    assert ("clearpass-core", "mcp_servers.clearpass") not in specs
    assert ("mist-core", "mcp_servers.mist") in specs
    assert ("apstra-core", "mcp_servers.apstra") in specs


def test_server_specs_can_include_all_optional_products(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)

    specs = ingest_tools._server_specs("all")

    assert ("clearpass-core", "mcp_servers.clearpass") in specs
    assert ("mist-core", "mcp_servers.mist") in specs
    assert ("apstra-core", "mcp_servers.apstra") in specs
    assert ("aos8-core", "mcp_servers.aos8") in specs
    assert ("edgeconnect-core", "mcp_servers.edgeconnect") in specs
