"""Regression tests for config.py error-body parsing and site-assignment fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_servers import config


def test_exc_resp_text_falls_back_to_str_when_no_response_attr():
    # CentralClient.post/post_async raise a bare Exception with no .response
    # attribute — the body text is baked into the message instead.
    exc = Exception('400 Bad Request — {"message": "duplicate VLAN"}')

    assert "duplicate" in config._exc_resp_text(exc).lower()


def test_exc_resp_text_prefers_response_text_when_present():
    exc = Exception("boom")
    exc.response = MagicMock(text="already exists")

    assert config._exc_resp_text(exc) == "already exists"


def test_create_vlan_upserts_on_duplicate_from_bare_post_exception(monkeypatch):
    client = MagicMock()
    client.post.side_effect = Exception('400 Bad Request — {"message": "duplicate VLAN"}')
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "_fetch_global_scope_id", lambda c: "GLOBAL")
    monkeypatch.setattr(config, "_post_scope_map", lambda *a, **k: None)

    result = config.create_vlan(vlan_id=100, vlan_name="test")

    client.put.assert_called_once()
    assert result["errors"] == []


def test_assign_device_to_site_skips_non_numeric_legacy_candidates(monkeypatch):
    client = MagicMock()
    calls = []

    def fake_request(method, endpoint, json=None):
        calls.append((method, endpoint, json))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    client._request.side_effect = fake_request
    monkeypatch.setattr(config, "get_client", lambda: client)

    result = config.assign_device_to_site("SN1", "Home-Lab-Site")

    # The first (New Central) candidate uses the raw site_id string and must
    # be attempted and succeed — no ValueError from int("Home-Lab-Site").
    assert calls == [
        ("POST", "/network-monitoring/v1/sites/Home-Lab-Site/devices", {"serials": ["SN1"]})
    ]
    assert result["errors"] == []
    assert result["response"] == {"ok": True}


def test_assign_device_to_site_reports_structured_errors_without_crashing(monkeypatch):
    client = MagicMock()
    calls = []

    def fake_request(method, endpoint, json=None):
        calls.append((method, endpoint, json))
        resp = MagicMock()
        resp.status_code = 404
        return resp

    client._request.side_effect = fake_request
    monkeypatch.setattr(config, "get_client", lambda: client)

    result = config.assign_device_to_site("SN1", "Home-Lab-Site")

    # First candidate is attempted and 404s; the two legacy candidates are
    # skipped (non-numeric site_id) rather than raising ValueError.
    assert len(calls) == 1
    assert result["response"] is None
    assert any("skipped" in e and "not numeric" in e for e in result["errors"])
