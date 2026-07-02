"""Regression tests for redact_sensitive clobbering bound_collection_response
pagination metadata ("_pagination.list_key" ends in the sensitive "_key" suffix)."""

from __future__ import annotations

from mcp_servers.shared import bound_collection_response, redact_sensitive


def test_redact_sensitive_preserves_pagination_list_key():
    payload = bound_collection_response({"items": [1, 2, 3], "count": 3}, limit=2)

    result = redact_sensitive(payload)

    assert result["_pagination"]["list_key"] == "items"
    assert result["_pagination"] == payload["_pagination"]


def test_redact_sensitive_still_redacts_real_secrets_alongside_pagination():
    payload = bound_collection_response(
        {"items": [{"api_key": "sk-live-abc123"}], "count": 1}, limit=2
    )

    result = redact_sensitive(payload)

    assert result["items"][0]["api_key"] == "******"
    assert result["_pagination"]["list_key"] == "items"


def test_redact_sensitive_still_redacts_top_level_key_field():
    assert redact_sensitive({"api_key": "secret"}) == {"api_key": "******"}
