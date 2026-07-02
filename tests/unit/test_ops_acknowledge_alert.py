"""Regression coverage for ops.acknowledge_alert (previously untested)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_servers import ops


def test_acknowledge_alert_returns_on_first_successful_candidate(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"acknowledged": True}
    client._request.return_value = resp
    monkeypatch.setattr(ops, "get_client", lambda: client)

    result = ops.acknowledge_alert("alert-1")

    client._request.assert_called_once_with(
        "POST",
        "/network-notifications/v1/alerts/acknowledge",
        json={"alert_id": ["alert-1"], "action": "ACK"},
    )
    assert result["endpoint_used"] == "/network-notifications/v1/alerts/acknowledge"
    assert result["response"] == {"acknowledged": True}
    assert result["errors"] == []


def test_acknowledge_alert_falls_back_through_candidates_on_404(monkeypatch):
    client = MagicMock()
    first = MagicMock(status_code=404)
    second = MagicMock(status_code=200)
    second.json.return_value = {"acknowledged": True}
    client._request.side_effect = [first, second]
    monkeypatch.setattr(ops, "get_client", lambda: client)

    result = ops.acknowledge_alert("alert-1", action="CLEAR")

    assert client._request.call_count == 2
    assert result["endpoint_used"] == "/network-notifications/v1/alerts/alert-1/acknowledge"
    assert result["response"] == {"acknowledged": True}


def test_acknowledge_alert_reports_structured_error_when_all_candidates_fail(monkeypatch):
    client = MagicMock()
    client._request.return_value = MagicMock(status_code=404)
    monkeypatch.setattr(ops, "get_client", lambda: client)

    result = ops.acknowledge_alert("alert-1")

    assert client._request.call_count == 3
    assert result["response"] is None
    assert any("no candidate path accepted" in e for e in result["errors"])
