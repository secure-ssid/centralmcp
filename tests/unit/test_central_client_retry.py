"""Unit tests for CentralClient._request retry behavior.

Covers the cautious-review test bar:
- 429 + Retry-After honored (integer and HTTP-date forms)
- 5xx retried with backoff for idempotent methods
- 4xx (non-429) NOT retried
- Max-attempts cap enforced
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from pipeline.clients.central_client import (
    CentralClient,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Retry-After parser
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_integer_seconds(self):
        assert _parse_retry_after("30") == 30.0

    def test_float_seconds(self):
        assert _parse_retry_after("2.5") == 2.5

    def test_negative_clamped_to_zero(self):
        assert _parse_retry_after("-5") == 0.0

    def test_http_date_in_future(self):
        # Build a date ~10s in the future; allow 2s slop for test timing.
        import email.utils, datetime
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=10)
        header = email.utils.format_datetime(future)
        parsed = _parse_retry_after(header)
        assert parsed is not None
        assert 7 < parsed < 12, f"expected ~10s, got {parsed}"

    def test_http_date_in_past_clamps_to_zero(self):
        assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0

    def test_unparseable_returns_none(self):
        assert _parse_retry_after("tomorrow") is None
        assert _parse_retry_after("") is None


# ---------------------------------------------------------------------------
# _request retry behavior
# ---------------------------------------------------------------------------


def _make_response(status_code, headers=None, text="{}"):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    r.text = text
    r.json.return_value = {}
    r.ok = 200 <= status_code < 300
    return r


def _make_client(responses):
    """Build a CentralClient whose session yields ``responses`` in sequence."""
    tm = MagicMock()
    tm.get_access_token.return_value = "fake-token"
    client = CentralClient(base_url="https://test.example.com", token_manager=tm)
    # Replace session with one that returns our scripted responses.
    client.session = MagicMock()
    client.session.headers = {}
    resp_iter = iter(responses)
    client.session.request.side_effect = lambda *a, **k: next(resp_iter)
    return client


class TestRetryBehavior:
    def test_429_honors_integer_retry_after(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client = _make_client([
            _make_response(429, {"Retry-After": "2"}),
            _make_response(200),
        ])
        resp = client._request("GET", "/x")
        assert resp.status_code == 200
        assert sleeps == [2.0]

    def test_429_without_header_uses_default_backoff(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client = _make_client([
            _make_response(429),
            _make_response(200),
        ])
        resp = client._request("GET", "/x")
        assert resp.status_code == 200
        # Legacy default is 60s.
        assert sleeps == [60.0]

    def test_5xx_retried_for_get(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client = _make_client([
            _make_response(503),
            _make_response(502),
            _make_response(200),
        ])
        resp = client._request("GET", "/x")
        assert resp.status_code == 200
        # Two retries, each waits >= 0.8s (with jitter) and <= 30s.
        assert len(sleeps) == 2
        for s in sleeps:
            assert 0.3 < s < 30.0

    def test_5xx_not_retried_for_post_by_default(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        client = _make_client([
            _make_response(503),
        ])
        resp = client._request("POST", "/x")
        assert resp.status_code == 503  # returned immediately, no retry

    def test_5xx_retried_for_post_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        client = _make_client([
            _make_response(503),
            _make_response(200),
        ])
        resp = client._request("POST", "/x", retry_5xx=True)
        assert resp.status_code == 200

    def test_4xx_non_429_not_retried(self, monkeypatch):
        """A 400/403/404 comes back immediately — don't retry client errors."""
        monkeypatch.setattr(time, "sleep", lambda s: None)
        for code in (400, 401, 403, 404):
            client = _make_client([_make_response(code)])
            resp = client._request("GET", "/x")
            assert resp.status_code == code

    def test_max_retries_cap_enforced(self, monkeypatch):
        """After max_retries, return the last response even if still transient."""
        monkeypatch.setattr(time, "sleep", lambda s: None)
        # max_retries=2 → up to 3 total attempts.
        client = _make_client([
            _make_response(503),
            _make_response(503),
            _make_response(503),
        ])
        resp = client._request("GET", "/x", max_retries=2)
        assert resp.status_code == 503

    def test_retry_after_clamped_to_max(self, monkeypatch):
        """Server says 'wait an hour' — we clamp to MAX_RETRY_DELAY (300s)."""
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        client = _make_client([
            _make_response(429, {"Retry-After": "3600"}),
            _make_response(200),
        ])
        client._request("GET", "/x")
        assert sleeps == [300.0]
