"""Regression test for detect_ssh_brute_force crashing on events with no timeAt."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_servers import monitoring


def _event(event_id, description, time_at=None):
    return {
        "eventId": event_id,
        "eventName": "ssh-login-failure",
        "description": description,
        "timeAt": time_at,
    }


def test_detect_ssh_brute_force_handles_events_missing_time_at(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_events.return_value = [
        _event("5210", "SSH login failed from 10.0.0.5", time_at=None),
        _event("5210", "SSH login failed from 10.0.0.5", time_at=None),
        _event("5214", "SSH session denied from 10.0.0.5", time_at=None),
    ]
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.detect_ssh_brute_force("SW1", min_failures=3)

    flagged = result["flagged_sources"]
    assert flagged
    assert flagged[0]["source_ip"] == "10.0.0.5"
    assert flagged[0]["failure_count"] == 3
    assert flagged[0]["first_seen"] is None
    assert flagged[0]["last_seen"] is None


def test_detect_ssh_brute_force_still_reports_times_when_present(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_events.return_value = [
        _event("5210", "SSH login failed from 10.0.0.5", time_at="2026-01-01T00:00:00Z"),
        _event("5210", "SSH login failed from 10.0.0.5", time_at="2026-01-01T00:05:00Z"),
        _event("5210", "SSH login failed from 10.0.0.5", time_at="2026-01-01T00:10:00Z"),
    ]
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.detect_ssh_brute_force("SW1", min_failures=3)

    flagged = result["flagged_sources"]
    assert flagged[0]["first_seen"] == "2026-01-01T00:00:00Z"
    assert flagged[0]["last_seen"] == "2026-01-01T00:10:00Z"
