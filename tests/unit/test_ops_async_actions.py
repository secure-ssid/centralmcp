import asyncio
from types import SimpleNamespace

from mcp_servers import ops


class _AcceptedContext:
    async def elicit(self, **kwargs):
        return SimpleNamespace(action="accept", data=SimpleNamespace(confirm=True))


class _Response:
    status_code = 202
    headers = {}

    def __init__(self, payload=None):
        self._payload = payload or {"accepted": True}

    def json(self):
        return self._payload


def test_reboot_device_uses_async_request_when_type_is_provided(monkeypatch):
    calls = []

    class FakeClient:
        async def _arequest(self, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return _Response()

        def _request(self, *args, **kwargs):
            raise AssertionError("sync _request should not be used")

    monkeypatch.setattr(ops, "get_client", lambda: FakeClient())

    result = asyncio.run(ops.reboot_device(_AcceptedContext(), "CX1", device_type="CX"))

    assert result == {
        "serial_number": "CX1",
        "device_type": "CX",
        "response": {"accepted": True},
        "errors": [],
    }
    assert calls == [("POST", "/network-troubleshooting/v1alpha1/cx/CX1/reboot", {"json": {}})]


def test_reboot_device_uses_async_request_after_auto_detection(monkeypatch):
    calls = []

    class FakeClient:
        async def _arequest(self, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return _Response()

        def _request(self, *args, **kwargs):
            raise AssertionError("sync _request should not be used")

    mcp_client = SimpleNamespace(get_device_by_serial=lambda serial: {"deviceType": "ACCESS_POINT"})
    monkeypatch.setattr(ops, "get_client", lambda: FakeClient())
    monkeypatch.setattr(ops, "get_mcp_client", lambda: mcp_client)

    result = asyncio.run(ops.reboot_device(_AcceptedContext(), "AP1"))

    assert result["device_type"] == "AP"
    assert calls == [("POST", "/network-troubleshooting/v1alpha1/aps/AP1/reboot", {"json": {}})]


def test_disconnect_client_uses_async_request_when_ap_serial_is_provided(monkeypatch):
    calls = []

    class FakeClient:
        async def _arequest(self, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return _Response()

        def _request(self, *args, **kwargs):
            raise AssertionError("sync _request should not be used")

    monkeypatch.setattr(ops, "get_client", lambda: FakeClient())

    result = asyncio.run(ops.disconnect_client(_AcceptedContext(), "aa:bb:cc:dd:ee:ff", ap_serial="AP1"))

    assert result == {
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "ap_serial": "AP1",
        "endpoint_used": "/network-troubleshooting/v1alpha1/aps/AP1/disconnectUserByMacAddress",
        "response": {"accepted": True},
        "errors": [],
    }
    assert calls == [
        (
            "POST",
            "/network-troubleshooting/v1alpha1/aps/AP1/disconnectUserByMacAddress",
            {"json": {"userMacAddress": "aa:bb:cc:dd:ee:ff"}},
        )
    ]


def test_disconnect_client_uses_async_request_after_auto_lookup(monkeypatch):
    calls = []

    class FakeClient:
        async def _arequest(self, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            return _Response()

        def _request(self, *args, **kwargs):
            raise AssertionError("sync _request should not be used")

    mcp_client = SimpleNamespace(
        find_client=lambda mac: {"connectedDeviceSerial": "AP2"},
    )
    monkeypatch.setattr(ops, "get_client", lambda: FakeClient())
    monkeypatch.setattr(ops, "get_mcp_client", lambda: mcp_client)

    result = asyncio.run(ops.disconnect_client(_AcceptedContext(), "aa:bb:cc:dd:ee:ff"))

    assert result["ap_serial"] == "AP2"
    assert calls == [
        (
            "POST",
            "/network-troubleshooting/v1alpha1/aps/AP2/disconnectUserByMacAddress",
            {"json": {"userMacAddress": "aa:bb:cc:dd:ee:ff"}},
        )
    ]
