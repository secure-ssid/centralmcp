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


def test_detect_ssh_brute_force_counts_ip_less_events_as_unattributed(monkeypatch):
    """Events with no parseable IPv4 (IPv6/hostname-only descriptions) must
    not be lumped into a single pseudo-attacker 'unknown' bucket."""
    mcp_client = MagicMock()
    mcp_client.get_events.return_value = [
        _event("5210", "SSH login failed from 2001:db8::1"),
        _event("5210", "SSH login failed from host-a.example"),
        _event("5214", "SSH session denied from 2001:db8::2"),
    ]
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.detect_ssh_brute_force("SW1", min_failures=3)

    assert result["flagged_sources"] == []
    assert result["unattributed_failures"] == 3
    assert result["total_ssh_failure_events"] == 3


def test_detect_ssh_brute_force_clamps_zero_min_failures(monkeypatch):
    """min_failures=0 must not flag every source that had a single failure."""
    mcp_client = MagicMock()
    mcp_client.get_events.return_value = []
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.detect_ssh_brute_force("SW1", min_failures=0)

    assert result["min_failures_threshold"] == 1


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
