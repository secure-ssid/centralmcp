from __future__ import annotations

import asyncio

import mcp_servers.ops as ops


def test_cx_ping_uses_async_troubleshooting_helper(monkeypatch):
    calls = []
    client = object()

    async def fake_atroubleshoot_async(received_client, endpoint, payload, errors):
        calls.append((received_client, endpoint, payload, errors))
        return {"status": "COMPLETED", "errors": errors}

    monkeypatch.setattr(ops, "get_client", lambda: client)
    monkeypatch.setattr(ops, "atroubleshoot_async", fake_atroubleshoot_async)

    result = asyncio.run(
        ops.cx_ping(
            "SERIAL1",
            "8.8.8.8",
            count=3,
            packet_size=128,
            vrf_name="mgmt",
            use_management_interface=True,
        )
    )

    assert result == {"status": "COMPLETED", "errors": []}
    assert calls == [
        (
            client,
            "/network-troubleshooting/v1alpha1/cx/SERIAL1/ping",
            {
                "destination": "8.8.8.8",
                "count": 3,
                "packetSize": 128,
                "vrfName": "mgmt",
                "useManagementInterface": True,
            },
            [],
        )
    ]


def test_cx_traceroute_uses_async_troubleshooting_helper(monkeypatch):
    calls = []
    client = object()

    async def fake_atroubleshoot_async(received_client, endpoint, payload, errors):
        calls.append((received_client, endpoint, payload, errors))
        return {"status": "COMPLETED", "errors": errors}

    monkeypatch.setattr(ops, "get_client", lambda: client)
    monkeypatch.setattr(ops, "atroubleshoot_async", fake_atroubleshoot_async)

    result = asyncio.run(
        ops.cx_traceroute(
            "SERIAL1",
            "1.1.1.1",
            vrf_name="mgmt",
            use_management_interface=False,
        )
    )

    assert result == {"status": "COMPLETED", "errors": []}
    assert calls == [
        (
            client,
            "/network-troubleshooting/v1alpha1/cx/SERIAL1/traceroute",
            {
                "destination": "1.1.1.1",
                "vrfName": "mgmt",
                "useManagementInterface": False,
            },
            [],
        )
    ]


def test_cx_show_validates_commands_before_async_call(monkeypatch):
    called = False

    async def fake_atroubleshoot_async(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(ops, "atroubleshoot_async", fake_atroubleshoot_async)

    result = asyncio.run(ops.cx_show("SERIAL1", ["configure terminal"]))

    assert result["status"] is None
    assert "must start with 'show '" in result["errors"][0]
    assert called is False


def test_cx_show_uses_async_troubleshooting_helper(monkeypatch):
    calls = []
    client = object()

    async def fake_atroubleshoot_async(received_client, endpoint, payload, errors):
        calls.append((received_client, endpoint, payload, errors))
        return {"status": "COMPLETED", "errors": errors}

    monkeypatch.setattr(ops, "get_client", lambda: client)
    monkeypatch.setattr(ops, "atroubleshoot_async", fake_atroubleshoot_async)

    result = asyncio.run(ops.cx_show("SERIAL1", ["show version"]))

    assert result == {"status": "COMPLETED", "errors": []}
    assert calls == [
        (
            client,
            "/network-troubleshooting/v1alpha1/cx/SERIAL1/showCommands",
            {"commands": ["show version"]},
            [],
        )
    ]
