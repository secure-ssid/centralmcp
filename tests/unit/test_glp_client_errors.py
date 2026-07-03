from __future__ import annotations

import httpx

from pipeline.clients.glp_client import _compact_exception_message


def test_compact_exception_message_supports_httpx_reason_phrase():
    request = httpx.Request("GET", "https://global.api.greenlake.hpe.com/devices")
    response = httpx.Response(
        429,
        json={"message": "rate limited"},
        request=request,
    )
    exc = httpx.HTTPStatusError("too many requests", request=request, response=response)

    message = _compact_exception_message(exc)

    assert message == "HTTP 429 Too Many Requests: {'message': 'rate limited'}"


def test_get_device_returns_none_only_for_empty_result():
    from unittest.mock import MagicMock

    from pipeline.clients.glp_client import GLPClient

    glp = GLPClient.__new__(GLPClient)
    glp._client = MagicMock()
    glp._client.get.return_value = {"items": []}

    assert glp.get_device("SN1") is None


def test_get_device_raises_on_transport_error_instead_of_masking_as_not_found():
    """Regression: transient failures (auth/5xx/network) returned None,
    indistinguishable from 'not found' — stages then told the operator the
    device was missing from GLP."""
    from unittest.mock import MagicMock

    import pytest as _pytest

    from pipeline.clients.glp_client import GLPClient

    glp = GLPClient.__new__(GLPClient)
    glp._client = MagicMock()
    glp._client.get.side_effect = RuntimeError("500 Internal Server Error")

    with _pytest.raises(RuntimeError, match="GLP device lookup failed"):
        glp.get_device("SN1")


def test_resolve_subscription_id_rejects_quote_injection():
    from unittest.mock import MagicMock

    import pytest as _pytest

    from pipeline.clients.glp_client import GLPClient

    glp = GLPClient.__new__(GLPClient)
    glp._client = MagicMock()

    with _pytest.raises(ValueError, match="Invalid subscription key"):
        glp._resolve_subscription_id("abc' or key ne '")

    glp._client.get.assert_not_called()
