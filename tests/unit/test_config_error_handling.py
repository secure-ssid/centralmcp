"""Regression tests for config.py error-body parsing and site-assignment fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_servers import config


def _post_exc(body: str) -> Exception:
    """Model the exception CentralClient.post raises (see _post_error):
    message embeds the body AND .response carries the real response."""
    exc = Exception(f"400 Bad Request — {body}")
    exc.response = MagicMock(text=body)
    return exc


def test_exc_resp_text_reads_response_text():
    assert "duplicate" in config._exc_resp_text(_post_exc('{"message": "duplicate VLAN"}')).lower()


def test_exc_resp_text_returns_empty_for_non_http_exception():
    # A non-HTTP failure whose message happens to contain an idempotency
    # marker must NOT be mistaken for a duplicate/already-exists response.
    exc = ValueError("could not serialize duplicate entry")

    assert config._exc_resp_text(exc) == ""


def test_create_vlan_upserts_on_duplicate_post_error(monkeypatch):
    client = MagicMock()
    client.post.side_effect = _post_exc('{"message": "duplicate VLAN"}')
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "_fetch_global_scope_id", lambda c: "GLOBAL")
    monkeypatch.setattr(config, "_post_scope_map", lambda *a, **k: None)

    result = config.create_vlan(vlan_id=100, vlan_name="test")

    client.put.assert_called_once()
    assert result["errors"] == []


def test_create_vlan_surfaces_non_http_failure_even_if_message_says_duplicate(monkeypatch):
    client = MagicMock()
    client.post.side_effect = ValueError("could not serialize duplicate entry")
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "_fetch_global_scope_id", lambda c: "GLOBAL")
    monkeypatch.setattr(config, "_post_scope_map", lambda *a, **k: None)

    result = config.create_vlan(vlan_id=100, vlan_name="test")

    client.put.assert_not_called()
    assert result["errors"]
    assert "duplicate entry" in result["errors"][0]


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
