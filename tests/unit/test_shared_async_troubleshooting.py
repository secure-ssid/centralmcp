import asyncio
from unittest.mock import MagicMock

from mcp_servers import shared


class _Response:
    status_code = 202
    headers = {"Location": "/network-troubleshooting/v1alpha1/cx/CX1/ping/async-operations/task-1"}

    def json(self):
        return {}


def test_atroubleshoot_async_uses_async_client_methods(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    class FakeClient:
        def __init__(self):
            self.post_calls = []
            self.get_calls = []

        async def _arequest(self, method, endpoint, **kwargs):
            self.post_calls.append((method, endpoint, kwargs))
            return _Response()

        async def aget(self, endpoint):
            self.get_calls.append(endpoint)
            return {"status": "COMPLETED"}

        def _request(self, *args, **kwargs):
            raise AssertionError("sync _request should not be used")

        def get(self, *args, **kwargs):
            raise AssertionError("sync get should not be used")

    monkeypatch.setattr(shared.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(shared, "_POLL_INTERVAL", 0.01)

    client = FakeClient()
    result = asyncio.run(
        shared.atroubleshoot_async(
            client,
            "/network-troubleshooting/v1alpha1/cx/CX1/ping",
            {"destination": "8.8.8.8"},
            [],
        )
    )

    assert result == {"status": "COMPLETED", "errors": []}
    assert client.post_calls == [
        (
            "POST",
            "/network-troubleshooting/v1alpha1/cx/CX1/ping",
            {"json": {"destination": "8.8.8.8"}},
        )
    ]
    assert client.get_calls == [
        "/network-troubleshooting/v1alpha1/cx/CX1/ping/async-operations/task-1"
    ]
    assert sleeps == [0.01]


def test_atroubleshoot_async_preserves_http_error_compaction():
    response = MagicMock()
    response.status_code = 400
    response.reason_phrase = "Bad Request"
    response.headers = {}
    response.text = "bad payload"
    response.json.side_effect = ValueError("not json")

    class FakeClient:
        async def _arequest(self, method, endpoint, **kwargs):
            return response

    result = asyncio.run(shared.atroubleshoot_async(FakeClient(), "/x", {}, []))

    assert result["status"] is None
    assert result["errors"] == ["HTTP 400: bad payload"]
