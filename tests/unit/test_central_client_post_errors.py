"""Regression tests for CentralClient.post/post_async error shape.

Unlike get/put/patch/delete (which use response.raise_for_status(), giving
callers a real httpx.HTTPStatusError with a .response attribute), post/
post_async used to raise a bare Exception with no .response — so every
pipeline caller doing getattr(exc, "response", None).text to detect
"duplicate"/"already exists" always saw "" and treated genuine idempotency
signals as hard failures.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from pipeline.clients.central_client import CentralClient


def _make_client(responses):
    tm = MagicMock()
    tm.get_access_token.return_value = "fake-token"
    client = CentralClient(base_url="https://test.example.com", token_manager=tm)
    client.session = MagicMock()
    client.session.headers = {}
    resp_iter = iter(responses)
    client.session.request.side_effect = lambda *a, **k: next(resp_iter)
    return client


def _make_httpx_response(status_code, text="{}"):
    return httpx.Response(
        status_code,
        content=text,
        request=httpx.Request("POST", "https://test.example.com/x"),
    )


def test_post_error_carries_response_with_body_text():
    client = _make_client([_make_httpx_response(400, '{"message": "duplicate VLAN"}')])

    try:
        client.post("/x", data={})
    except Exception as exc:
        assert exc.response is not None
        assert "duplicate" in exc.response.text.lower()
    else:
        raise AssertionError("expected an exception")


def test_post_error_message_still_includes_body_for_logs():
    client = _make_client([_make_httpx_response(400, '{"message": "duplicate VLAN"}')])

    try:
        client.post("/x", data={})
    except Exception as exc:
        assert "400" in str(exc)
        assert "duplicate VLAN" in str(exc)
    else:
        raise AssertionError("expected an exception")


def test_post_async_error_carries_response_with_body_text():
    client = _make_client([_make_httpx_response(409, '{"message": "already exists"}')])

    try:
        client.post_async("/x", data={})
    except Exception as exc:
        assert exc.response is not None
        assert "already exists" in exc.response.text.lower()
    else:
        raise AssertionError("expected an exception")
