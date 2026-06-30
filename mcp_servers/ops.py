"""MCP server — Aruba Central ops: troubleshooting and device actions (22 tools).

Covers: CX/AOS-S/Gateway ping/traceroute/show, PoE bounce, port bounce, cable test,
reboot, disconnect client, acknowledge alert, LLDP neighbors, ARP table, MAC table,
speed test, find MAC on switch, port error counters, spanning tree, interface counters.
"""
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel

from mcp_servers.shared import (
    DIAGNOSTIC,
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    _AOS_S_BASE,
    _CX_TROUBLESHOOTING_BASE,
    _GATEWAY_BASE,
    atroubleshoot_async,
    compact_http_error,
    device_type_for_troubleshoot,
    get_client,
    get_mcp_client,
)

mcp = FastMCP("aruba-ops")


class _ConfirmAction(BaseModel):
    confirm: bool = False


async def _cx_show_commands(serial_number: str, commands: list[str]) -> dict[str, Any]:
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/showCommands",
        {"commands": commands},
        errors,
    )


# ── CX Troubleshooting ────────────────────────────────────────────────────────

@mcp.tool(annotations=DIAGNOSTIC)
async def cx_ping(
    serial_number: str,
    destination: str,
    count: int | None = None,
    packet_size: int | None = None,
    vrf_name: str | None = None,
    use_management_interface: bool | None = None,
) -> dict[str, Any]:
    """Ping a destination from a CX switch and return the result (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    payload: dict[str, Any] = {"destination": destination}
    if count is not None:
        payload["count"] = count
    if packet_size is not None:
        payload["packetSize"] = packet_size
    if vrf_name is not None:
        payload["vrfName"] = vrf_name
    if use_management_interface is not None:
        payload["useManagementInterface"] = use_management_interface

    return await atroubleshoot_async(
        client,
        f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/ping",
        payload,
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def cx_traceroute(
    serial_number: str,
    destination: str,
    vrf_name: str | None = None,
    use_management_interface: bool | None = None,
) -> dict[str, Any]:
    """Run a traceroute from a CX switch (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    payload: dict[str, Any] = {"destination": destination}
    if vrf_name is not None:
        payload["vrfName"] = vrf_name
    if use_management_interface is not None:
        payload["useManagementInterface"] = use_management_interface

    return await atroubleshoot_async(
        client,
        f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/traceroute",
        payload,
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def cx_show(
    serial_number: str,
    commands: list[str],
) -> dict[str, Any]:
    """Run 'show' commands on a CX switch (all must start with 'show ', max 20, async polls ~60s)."""
    if not commands:
        return {"status": None, "errors": ["commands list cannot be empty"]}
    if len(commands) > 20:
        return {"status": None, "errors": [f"commands list cannot exceed 20 items (got {len(commands)})"]}
    for i, cmd in enumerate(commands):
        if not cmd.strip().lower().startswith("show "):
            return {"status": None, "errors": [f"Command {i} must start with 'show ': '{cmd}'"]}

    return await _cx_show_commands(serial_number, commands)


# ── AOS-S Troubleshooting ─────────────────────────────────────────────────────

@mcp.tool(annotations=DIAGNOSTIC)
async def aos_s_ping(serial_number: str, destination: str) -> dict[str, Any]:
    """Ping a destination from an AOS-S switch (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_AOS_S_BASE}/{serial_number}/ping",
        {"destination": destination},
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def aos_s_traceroute(serial_number: str, destination: str) -> dict[str, Any]:
    """Run a traceroute from an AOS-S switch (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_AOS_S_BASE}/{serial_number}/traceroute",
        {"destination": destination},
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def aos_s_show(serial_number: str, commands: list[str]) -> dict[str, Any]:
    """Run 'show' commands on an AOS-S switch (all must start with 'show ', async polls ~60s)."""
    if not commands:
        return {"status": None, "errors": ["commands list cannot be empty"]}
    for i, cmd in enumerate(commands):
        if not cmd.strip().lower().startswith("show "):
            return {"status": None, "errors": [f"Command {i} must start with 'show ': '{cmd}'"]}
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_AOS_S_BASE}/{serial_number}/showCommands",
        {"commands": commands},
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def gateway_show(serial_number: str, commands: list[str]) -> dict[str, Any]:
    """Run 'show' commands on an Aruba gateway via async troubleshooting API. Each must start with 'show '."""
    if not commands:
        return {"status": None, "errors": ["commands list cannot be empty"]}
    for i, cmd in enumerate(commands):
        if not cmd.strip().lower().startswith("show "):
            return {"status": None, "errors": [f"Command {i} must start with 'show ': '{cmd}'"]}
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_GATEWAY_BASE}/{serial_number}/showCommands",
        {"commands": commands},
        errors,
    )


@mcp.tool(annotations=DIAGNOSTIC)
async def aos_s_arp(serial_number: str) -> dict[str, Any]:
    """Get the ARP table from an AOS-S switch (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(client, f"{_AOS_S_BASE}/{serial_number}/getArpTable", {}, errors)


# aos_s_locate was removed: per pycentral's
# TROUBLESHOOTING_METHOD_DEVICE_MAPPING, `locate` is only supported on
# cx / aps / gateways, not aos-s. Hitting /network-troubleshooting/v1alpha1/
# aos-s/{serial}/locate always returns "Device not found" — it's not a
# real endpoint.
#
# If AOS-S locate is needed in the future, route it differently (CLI over
# the show-commands path, or a classic-central /network-actions call).


# ── PoE / Port / Cable Ops ────────────────────────────────────────────────────

@mcp.tool(annotations=DESTRUCTIVE)
async def poe_bounce(
    ctx: Context,
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Power-cycle PoE on switch/gateway ports (async, polls ~60s).

    ports format: CX "1/1/1", AOS-S "1", Gateway "GE 0/0/0". device_type auto-detected.
    """
    client = get_client()
    errors: list[str] = []
    dtype = device_type_for_troubleshoot(serial_number, device_type)
    if dtype is None or dtype == "aps":
        errors.append("PoE bounce is not supported on Access Points.")
        return {"status": None, "errors": errors}

    try:
        result = await ctx.elicit(
            message=f"⚠️ Confirm PoE BOUNCE on {serial_number} ports {ports}? Connected devices will temporarily lose power.",
            schema=_ConfirmAction,
        )
    except Exception as exc:
        return {"status": "CONFIRMATION_UNAVAILABLE", "error": f"client does not support elicitation; operation NOT performed: {exc}"}
    if result.action != "accept" or not result.data.confirm:
        return {"status": "CANCELLED", "detail": "user declined confirmation"}

    return await atroubleshoot_async(client, f"/network-troubleshooting/v1alpha1/{dtype}/{serial_number}/poeBounce", {"ports": ports}, errors)


@mcp.tool(annotations=DESTRUCTIVE)
async def port_bounce(
    ctx: Context,
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Link-reset (bounce) switch/gateway ports (async, polls ~60s).

    ports format: CX "1/1/1", AOS-S "1", Gateway "GE 0/0/0". device_type auto-detected.
    """
    client = get_client()
    errors: list[str] = []
    dtype = device_type_for_troubleshoot(serial_number, device_type)
    if dtype is None or dtype == "aps":
        errors.append("Port bounce is not supported on Access Points.")
        return {"status": None, "errors": errors}

    try:
        result = await ctx.elicit(
            message=f"⚠️ Confirm PORT BOUNCE on {serial_number} ports {ports}? Connected devices will lose connectivity.",
            schema=_ConfirmAction,
        )
    except Exception as exc:
        return {"status": "CONFIRMATION_UNAVAILABLE", "error": f"client does not support elicitation; operation NOT performed: {exc}"}
    if result.action != "accept" or not result.data.confirm:
        return {"status": "CANCELLED", "detail": "user declined confirmation"}

    return await atroubleshoot_async(client, f"/network-troubleshooting/v1alpha1/{dtype}/{serial_number}/portBounce", {"ports": ports}, errors)


@mcp.tool(annotations=DIAGNOSTIC)
async def cable_test(
    serial_number: str,
    ports: list[str],
    device_type: str | None = None,
) -> dict[str, Any]:
    """Run a cable/TDR test on CX or AOS-S switch ports (async, polls ~60s)."""
    client = get_client()
    errors: list[str] = []
    dtype = device_type_for_troubleshoot(serial_number, device_type)
    if dtype == "gateways":
        errors.append("Cable test is not supported on gateways.")
        return {"status": None, "errors": errors}
    if dtype is None or dtype == "aps":
        errors.append("Cable test is not supported on Access Points.")
        return {"status": None, "errors": errors}
    return await atroubleshoot_async(
        client,
        f"/network-troubleshooting/v1alpha1/{dtype}/{serial_number}/cableTest",
        {"ports": ports},
        errors,
    )


# ── Device Actions ────────────────────────────────────────────────────────────

@mcp.tool(annotations=DESTRUCTIVE)
async def reboot_device(
    ctx: Context,
    serial_number: str,
    device_type: str | None = None,
) -> dict[str, Any]:
    """Reboot an AP, CX switch, AOS-S switch, or gateway. device_type auto-detected if omitted."""
    client = get_client()
    errors: list[str] = []

    if not device_type:
        device = get_mcp_client().get_device_by_serial(serial_number)
        if device:
            raw = device.get("deviceType", "")
            if "ACCESS_POINT" in raw or raw == "AP":
                device_type = "AP"
            elif "SWITCH" in raw:
                device_type = "SWITCH"
            elif "GATEWAY" in raw:
                device_type = "GATEWAY"
        if not device_type:
            errors.append(f"Could not determine device type for {serial_number}. Provide device_type explicitly.")
            return {"serial_number": serial_number, "device_type": None, "response": None, "errors": errors}

    dt = device_type.upper()
    if dt in ("AP", "ACCESS_POINT"):
        endpoint = f"/network-troubleshooting/v1alpha1/aps/{serial_number}/reboot"
    elif dt in ("CX", "SWITCH"):
        endpoint = f"/network-troubleshooting/v1alpha1/cx/{serial_number}/reboot"
    elif dt in ("AOS-S", "AOSS", "AOS_S"):
        endpoint = f"{_AOS_S_BASE}/{serial_number}/reboot"
    elif dt in ("GATEWAY", "GW"):
        endpoint = f"/network-troubleshooting/v1alpha1/gateways/{serial_number}/reboot"
    else:
        errors.append(f"Unknown device_type '{device_type}'. Use 'AP', 'CX', 'AOS-S', or 'GATEWAY'.")
        return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}

    try:
        result = await ctx.elicit(
            message=f"⚠️ Confirm REBOOT of {device_type} {serial_number}? This will cause a service interruption.",
            schema=_ConfirmAction,
        )
    except Exception as exc:
        return {"status": "CONFIRMATION_UNAVAILABLE", "error": f"client does not support elicitation; operation NOT performed: {exc}"}
    if result.action != "accept" or not result.data.confirm:
        return {"status": "CANCELLED", "detail": "user declined confirmation"}

    try:
        response = client._request("POST", endpoint, json={})
        if response.status_code not in (200, 201, 202):
            errors.append(compact_http_error(response))
            return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"serial_number": serial_number, "device_type": device_type, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"serial_number": serial_number, "device_type": device_type, "response": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
async def disconnect_client(
    ctx: Context,
    mac_address: str,
    ap_serial: str | None = None,
) -> dict[str, Any]:
    """Force-disconnect a wireless client by MAC address. ap_serial auto-looked up if omitted."""
    client = get_client()
    errors: list[str] = []

    # Resolve AP serial if not provided
    if not ap_serial:
        cl = get_mcp_client().find_client(mac_address)
        if not cl:
            return {"mac_address": mac_address, "response": None, "errors": ["Client not found in monitoring"]}
        ap_serial = cl.get("connectedDeviceSerial")
        if not ap_serial:
            return {"mac_address": mac_address, "response": None, "errors": ["Could not determine connected AP serial"]}

    try:
        result = await ctx.elicit(
            message=f"⚠️ Confirm DISCONNECT of client {mac_address}? The client will be forced off the network.",
            schema=_ConfirmAction,
        )
    except Exception as exc:
        return {"status": "CONFIRMATION_UNAVAILABLE", "error": f"client does not support elicitation; operation NOT performed: {exc}"}
    if result.action != "accept" or not result.data.confirm:
        return {"status": "CANCELLED", "detail": "user declined confirmation"}

    endpoint = f"/network-troubleshooting/v1alpha1/aps/{ap_serial}/disconnectUserByMacAddress"
    try:
        response = client._request("POST", endpoint, json={"userMacAddress": mac_address})
        if response.status_code not in (200, 201, 202):
            errors.append(compact_http_error(response))
            return {"mac_address": mac_address, "response": None, "errors": errors}
        try:
            resp_body = response.json()
        except Exception:
            resp_body = {}
        return {"mac_address": mac_address, "ap_serial": ap_serial, "endpoint_used": endpoint, "response": resp_body, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"mac_address": mac_address, "response": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def acknowledge_alert(
    alert_id: str,
    action: str = "ACK",
) -> dict[str, Any]:
    """Acknowledge, clear, or resolve an active alert. action: ACK/CLEAR/RESOLVE.

    KNOWN ISSUE (2026-04): all candidate paths 404 on this tenant — no peer MCP
    wraps this either. Tool preserved for structured 'not available' response.
    """
    client = get_client()
    errors: list[str] = []

    candidates = [
        ("POST", "/network-notifications/v1/alerts/acknowledge", {"alert_id": [alert_id], "action": action}),
        ("POST", f"/network-notifications/v1/alerts/{alert_id}/acknowledge", {"action": action}),
        ("PATCH", f"/network-notifications/v1/alerts/{alert_id}", {"status": action}),
    ]

    for method, endpoint, payload in candidates:
        try:
            response = client._request(method, endpoint, json=payload)
            if response.status_code == 404:
                errors.append(f"404 at {endpoint}")
                continue
            if response.status_code not in (200, 201, 202):
                errors.append(compact_http_error(response, endpoint=endpoint))
                continue
            try:
                resp_body = response.json()
            except Exception:
                resp_body = {}
            return {"alert_id": alert_id, "action": action, "endpoint_used": endpoint, "response": resp_body, "errors": errors}
        except Exception as exc:
            errors.append(str(exc))

    errors.append(
        "acknowledge_alert: no candidate path accepted the request. "
        "This endpoint may not be exposed on New Central; track "
        "https://developer.arubanetworks.com/new-central/reference for updates."
    )
    return {"alert_id": alert_id, "action": action, "response": None, "errors": errors}


# ── CX Switch Intelligence ────────────────────────────────────────────────────

@mcp.tool(annotations=DIAGNOSTIC)
async def get_lldp_neighbors(serial_number: str) -> dict[str, Any]:
    """Get LLDP neighbor table from a CX switch.

    Shows what device is connected to each port — hostname, port ID, system
    capabilities, and management address. Useful for instantly identifying what's
    plugged into a port without guessing from MAC or client data.
    """
    return await _cx_show_commands(serial_number, ["show lldp neighbors"])


@mcp.tool(annotations=DIAGNOSTIC)
async def get_cx_arp_table(serial_number: str) -> dict[str, Any]:
    """Get the ARP table from a CX switch.

    Returns IP-to-MAC mappings with interface and VLAN. Useful for resolving
    an IP to a MAC when a client doesn't appear in Central's client list.
    """
    return await _cx_show_commands(serial_number, ["show arp"])


@mcp.tool(annotations=DIAGNOSTIC)
async def get_cx_mac_table(
    serial_number: str,
    interface: str | None = None,
) -> dict[str, Any]:
    """Get the MAC address table from a CX switch.

    Shows which MAC addresses are learned on which ports and VLANs. When
    interface is provided (e.g. '1/1/16'), filters to that port only.
    Useful for tracing exactly which port a device is connected to.
    """
    cmd = f"show mac-address-table interface {interface}" if interface else "show mac-address-table"
    return await _cx_show_commands(serial_number, [cmd])


@mcp.tool(annotations=DIAGNOSTIC)
async def find_mac_on_switch(serial_number: str, mac_address: str) -> dict[str, Any]:
    """Find which port a MAC address is learned on for a CX switch.

    Runs 'show mac-address-table address <mac>' and returns the port, VLAN,
    and entry type. The fastest way to answer "what port is device X on?"
    """
    mac_clean = mac_address.replace("-", ":").lower()
    result = await _cx_show_commands(serial_number, [f"show mac-address-table address {mac_clean}"])
    result["mac_address"] = mac_address
    return result


@mcp.tool(annotations=DIAGNOSTIC)
async def get_switch_port_errors(serial_number: str, interface: str | None = None) -> dict[str, Any]:
    """Get error counters for CX switch ports.

    Returns CRC errors, input errors, output errors, runts, giants, and
    collisions. When interface is given (e.g. '1/1/5') only that port is
    queried; otherwise all interfaces. First thing to check for a flapping
    or slow port.
    """
    cmd = (
        f"show interface {interface} statistics"
        if interface
        else "show interface statistics"
    )
    return await _cx_show_commands(serial_number, [cmd])


@mcp.tool(annotations=DIAGNOSTIC)
async def get_switch_spanning_tree(
    serial_number: str,
    interface: str | None = None,
) -> dict[str, Any]:
    """Get spanning tree topology for a CX switch.

    Returns bridge ID, root bridge, port roles (Root/Designated/Alternate/
    Backup), port states (Forwarding/Blocking/Learning), and timers.
    When interface is provided only that port's STP detail is returned.
    Essential for diagnosing broadcast storms, topology changes, and loops.
    """
    cmd = (
        f"show spanning-tree detail interface {interface}"
        if interface
        else "show spanning-tree detail"
    )
    return await _cx_show_commands(serial_number, [cmd])


@mcp.tool(annotations=DIAGNOSTIC)
async def get_switch_interface_counters(
    serial_number: str,
    interface: str | None = None,
) -> dict[str, Any]:
    """Get Tx/Rx byte and packet counters for CX switch interfaces.

    Returns transmitted/received bytes, unicast/multicast/broadcast packet
    counts and rates. When interface is provided (e.g. '1/1/1') only that
    port is returned. Use for capacity analysis and saturation detection.
    """
    cmd = (
        f"show interface {interface} counters"
        if interface
        else "show interface counters"
    )
    return await _cx_show_commands(serial_number, [cmd])


@mcp.tool(annotations=DIAGNOSTIC)
async def run_speed_test(serial_number: str) -> dict[str, Any]:
    """Run a speed test from an AP to measure uplink bandwidth.

    Uses the Central async troubleshooting API. Returns download/upload
    throughput and latency from the AP's perspective. Useful for verifying
    whether a slow client experience is a radio issue or an uplink issue.
    """
    _AP_TROUBLESHOOTING_BASE = "/network-troubleshooting/v1alpha1/aps"
    client = get_client()
    errors: list[str] = []
    return await atroubleshoot_async(
        client,
        f"{_AP_TROUBLESHOOTING_BASE}/{serial_number}/speedtest",
        {},
        errors,
    )


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    from mcp_servers.shared import run_server
    run_server(mcp)
