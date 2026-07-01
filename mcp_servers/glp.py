"""MCP server — GreenLake Platform (GLP): inventory, licensing, and user management (12 tools).

Covers: GLP device lifecycle, subscription assignment, bulk onboarding, audit logs, users.
Uses the target_account (glp_account) credentials.
"""
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    bound_collection_response,
    get_glp_client,
    safe_api_path,
)
from pipeline.clients.glp_client import _V2BETA1_WRITES_FLAG, _writes_enabled

mcp = FastMCP("aruba-glp")

_GLP_GET_PREFIXES = (
    "/devices/",
    "/subscriptions/",
    "/audit-logs/",
    "/identity/",
    "/service-catalog/",
    "/workspaces/",
    "/reporting/",
)


@mcp.tool(annotations=READ_ONLY)
def glp_write_status() -> dict[str, Any]:
    """Report whether guarded GLP v2beta1 write tools are enabled."""
    enabled = _writes_enabled()
    return {
        "enabled": enabled,
        "flag": _V2BETA1_WRITES_FLAG,
        "set_to_enable": f"{_V2BETA1_WRITES_FLAG}=1",
        "guarded_tools": [
            "glp_assign_subscription",
            "glp_add_device",
            "glp_add_devices_bulk",
            "glp_archive_device",
        ],
        "message": (
            "GLP write tools can execute."
            if enabled
            else "GLP write tools are visible but fail closed until the feature flag is enabled."
        ),
    }


def _write_disabled(tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if _writes_enabled():
        return None
    return {
        "status": "FORBIDDEN",
        "error": (
            f"{tool_name} is gated behind {_V2BETA1_WRITES_FLAG}=1 and was not performed. "
            "Set the flag only after sandbox-validating payload and rollback."
        ),
        "flag": _V2BETA1_WRITES_FLAG,
        "would_have_sent": payload,
    }


@mcp.tool(annotations=READ_ONLY)
def glp_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a guarded read-only GET against selected GLP API families.

    Useful for exploring GLP service-catalog, workspaces, reporting, and
    adjacent read-only APIs before adding dedicated typed wrappers. Path must
    be relative and begin with one of the documented GLP API family prefixes.
    List payloads are bounded with `limit` and `offset`.
    """
    try:
        safe_path = safe_api_path(path, _GLP_GET_PREFIXES)
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    try:
        data = get_glp_client()._client.get(safe_path, params=params or {})
        data = bound_collection_response(data, limit=limit, offset=offset)
        return {"data": data, "endpoint_used": safe_path}
    except Exception as exc:
        return {"error": str(exc), "endpoint_used": safe_path}


@mcp.tool(annotations=READ_ONLY)
def list_glp_devices(limit: int = 100, filter: str | None = None) -> dict[str, Any]:
    """List devices in the GLP workspace (warranty, subscription state, lifecycle).

    Args:
        filter: OData filter, e.g. "serialNumber eq 'SG30LMR164'".
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_devices(limit=limit, filter=filter)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_device(serial_number: str) -> dict[str, Any]:
    """Fetch a single device from GLP by serial number."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        device = glp.get_device(serial_number)
        return {"device": device, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"device": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_glp_subscriptions(limit: int = 100) -> dict[str, Any]:
    """List subscriptions (license keys) in the GLP workspace (type, assigned device, expiry)."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_subscriptions(limit=limit)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_subscription(subscription_id: str) -> dict[str, Any]:
    """Fetch a single GLP subscription by ID."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        sub = glp.get_subscription(subscription_id)
        return {"subscription": sub, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"subscription": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_glp_users(limit: int = 300) -> dict[str, Any]:
    """List users with access to the GLP workspace."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_users(limit=limit)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_glp_audit_logs(limit: int = 100, category: str | None = None) -> dict[str, Any]:
    """List GLP audit log entries (who did what and when).

    Args:
        category: e.g. "USER_MANAGEMENT", "DEVICE_MANAGEMENT".
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_audit_logs(limit=limit, category=category)
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def glp_assign_subscription(serial_number: str, subscription_key: str) -> dict[str, Any]:
    """Assign a GLP subscription (license) to a device.

    subscription_key accepts either a subscription key string or its GLP UUID;
    a key is resolved to its UUID internally before assignment.
    """
    disabled = _write_disabled(
        "glp_assign_subscription",
        {"serial_number": serial_number, "subscription_key": subscription_key},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.assign_subscription(serial_number, subscription_key)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def glp_add_device(serial_number: str, mac_address: str | None = None) -> dict[str, Any]:
    """Add a device to the GLP workspace (async task, polls until complete, ~5min max)."""
    disabled = _write_disabled(
        "glp_add_device",
        {"serial_number": serial_number, "mac_address": mac_address},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        task_id = glp.add_device(serial_number, mac_address=mac_address)
        task_result = glp.poll_task(task_id)
        return {"task_id": task_id, "task_result": task_result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"task_id": None, "task_result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def glp_add_devices_bulk(devices: list[dict[str, str]]) -> dict[str, Any]:
    """Bulk add devices to GLP. devices: dicts with 'serialNumber' and 'macAddress'.

    Returns task_id + task_result (successfulDevicesSerial / failedDevicesSerial).
    """
    disabled = _write_disabled("glp_add_devices_bulk", {"devices": devices})
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        task_id = glp.add_devices(devices)
        task_result = glp.poll_task(task_id)
        return {"task_id": task_id, "task_result": task_result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"task_id": None, "task_result": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
def glp_archive_device(serial_number: str) -> dict[str, Any]:
    """Archive a device in GLP (removes from Central, keeps in GLP inventory)."""
    disabled = _write_disabled("glp_archive_device", {"serial_number": serial_number})
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.archive_device(serial_number)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    stable_list_tools(mcp)
    from mcp_servers.shared import run_server
    run_server(mcp)
