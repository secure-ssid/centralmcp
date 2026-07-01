"""MCP server — GreenLake Platform (GLP): inventory, licensing, users, and service catalog (31 tools).

Covers: GLP device lifecycle, subscription assignment, bulk onboarding, audit logs, users,
workspaces, reporting statuses, and service-catalog reads.
Uses the target_account (glp_account) credentials.
"""
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    IDEMPOTENT_WRITE,
    READ_ONLY,
    bound_collection_response,
    clamp_limit,
    get_glp_client,
    safe_api_path,
)
from pipeline.clients.glp_client import _V2BETA1_WRITES_FLAG, _writes_enabled

mcp = FastMCP("aruba-glp")

_GLP_GET_PREFIXES = (
    "/devices/",
    "/subscriptions/",
    "/audit-log/",
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


def _path_part(value: str) -> str:
    return quote(str(value), safe="")


def _params(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _paged_params(limit: int | None = 100, offset: int | None = 0, **values: Any) -> dict[str, Any]:
    return {
        **_params(**values),
        "limit": clamp_limit(limit),
        "offset": max(0, offset or 0),
    }


def _cursor_params(
    limit: int | None = 100,
    next_cursor: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    return {
        **_params(next=next_cursor, **values),
        "limit": clamp_limit(limit),
    }


def _glp_read(
    path: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        safe_api_path(path, _GLP_GET_PREFIXES)
    except ValueError as exc:
        return {"data": None, "endpoint_used": path, "errors": [f"Invalid path. {exc}"]}
    try:
        client = get_glp_client()._client
        if headers:
            response = client._request("GET", path, params=params or {}, headers=headers)
            response.raise_for_status()
            data = response.json()
        else:
            data = client.get(path, params=params or {})
        return {"data": data, "endpoint_used": path, "errors": []}
    except Exception as exc:
        return {"data": None, "endpoint_used": path, "errors": [str(exc)]}


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
def list_glp_devices(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List devices in the GLP workspace (warranty, subscription state, lifecycle).

    Args:
        limit: Maximum items to request; clamped to the MCP list limit.
        offset: Zero-based result offset for pagination.
        filter: OData filter, e.g. "serialNumber eq 'SG30LMR164'".
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_devices(limit=clamp_limit(limit), offset=max(0, offset), filter=filter)
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
def get_glp_device_by_id(device_id: str) -> dict[str, Any]:
    """Fetch a GLP device by its official device resource ID."""
    return _glp_read(f"/devices/v1/devices/{_path_part(device_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_subscriptions(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List subscriptions with `limit` / `offset` pagination."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_subscriptions(limit=clamp_limit(limit), offset=max(0, offset))
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
def list_glp_users(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List users with access to the GLP workspace using `limit` / `offset` pagination."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_users(limit=clamp_limit(limit), offset=max(0, offset))
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_user(user_id: str) -> dict[str, Any]:
    """Fetch a single GLP identity user by ID."""
    return _glp_read(f"/identity/v1/users/{_path_part(user_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_audit_logs(
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
) -> dict[str, Any]:
    """List GLP audit log entries (who did what and when).

    Args:
        limit: Maximum entries to request; clamped to the MCP list limit.
        offset: Zero-based result offset for pagination.
        category: e.g. "USER_MANAGEMENT", "DEVICE_MANAGEMENT".
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_audit_logs(
            limit=clamp_limit(limit),
            offset=max(0, offset),
            category=category,
        )
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_audit_log_detail(audit_log_id: str) -> dict[str, Any]:
    """Fetch official GLP audit-log details for entries with details enabled."""
    return _glp_read(f"/audit-log/v1/logs/{_path_part(audit_log_id)}/detail")


@mcp.tool(annotations=READ_ONLY)
def get_glp_workspace(workspace_id: str) -> dict[str, Any]:
    """Fetch basic GreenLake workspace information by workspace ID."""
    return _glp_read(f"/workspaces/v1/workspaces/{_path_part(workspace_id)}")


@mcp.tool(annotations=READ_ONLY)
def get_glp_workspace_contact(workspace_id: str) -> dict[str, Any]:
    """Fetch detailed GreenLake workspace contact information."""
    return _glp_read(f"/workspaces/v1/workspaces/{_path_part(workspace_id)}/contact")


@mcp.tool(annotations=READ_ONLY)
def list_glp_reporting_statuses(
    filter: str | None = None,
    sort: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List GreenLake reporting status records with bounded pagination."""
    return _glp_read(
        "/reporting/v1/statuses",
        _paged_params(limit, offset, filter=filter, sort=sort),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_reporting_status(status_id: str) -> dict[str, Any]:
    """Fetch a single GreenLake reporting status record by ID."""
    return _glp_read(f"/reporting/v1/statuses/{_path_part(status_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_service_offers(
    next_cursor: str | None = None,
    limit: int = 100,
    filter: str | None = None,
) -> dict[str, Any]:
    """List GreenLake service-catalog offers with cursor pagination."""
    return _glp_read(
        "/service-catalog/v1beta1/service-offers",
        _cursor_params(limit, next_cursor, filter=filter),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_offer(offer_id: str) -> dict[str, Any]:
    """Fetch a GreenLake service-catalog offer by ID."""
    return _glp_read(f"/service-catalog/v1beta1/service-offers/{_path_part(offer_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_service_offer_regions(
    next_cursor: str | None = None,
    limit: int = 100,
    filter: str | None = None,
) -> dict[str, Any]:
    """List GreenLake service-offer regions with cursor pagination."""
    return _glp_read(
        "/service-catalog/v1beta1/service-offer-regions",
        _cursor_params(limit, next_cursor, filter=filter),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_offer_region(region_id: str) -> dict[str, Any]:
    """Fetch a GreenLake service-offer region by ID."""
    return _glp_read(f"/service-catalog/v1beta1/service-offer-regions/{_path_part(region_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_service_provisions(
    workspace_id: str | None = None,
    next_cursor: str | None = None,
    limit: int = 100,
    filter: str | None = None,
    unredacted: bool | None = None,
    all_workspaces: bool | None = None,
) -> dict[str, Any]:
    """List GreenLake service provisions, optionally scoped by workspace ID."""
    headers = {"Hpe-workspace-id": workspace_id} if workspace_id else None
    return _glp_read(
        "/service-catalog/v1beta1/service-provisions",
        _cursor_params(
            limit,
            next_cursor,
            filter=filter,
            unredacted=unredacted,
            all=all_workspaces,
        ),
        headers=headers,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_provision(
    provision_id: str,
    unredacted: bool | None = None,
) -> dict[str, Any]:
    """Fetch a GreenLake service provision by ID."""
    return _glp_read(
        f"/service-catalog/v1beta1/service-provisions/{_path_part(provision_id)}",
        _params(unredacted=unredacted),
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_service_managers(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List GreenLake service managers."""
    return _glp_read(
        "/service-catalog/v1/service-managers",
        _paged_params(limit, offset),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_manager(manager_id: str) -> dict[str, Any]:
    """Fetch a GreenLake service manager by ID."""
    return _glp_read(f"/service-catalog/v1/service-managers/{_path_part(manager_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_service_manager_provisions(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List GreenLake service-manager provisions."""
    return _glp_read(
        "/service-catalog/v1/service-manager-provisions",
        _paged_params(limit, offset, filter=filter),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_manager_provision(provision_id: str) -> dict[str, Any]:
    """Fetch a GreenLake service-manager provision by ID."""
    return _glp_read(f"/service-catalog/v1/service-manager-provisions/{_path_part(provision_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_per_region_service_managers(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List GreenLake per-region service-manager mappings."""
    return _glp_read(
        "/service-catalog/v1/per-region-service-managers",
        _paged_params(limit, offset, filter=filter),
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_managers_for_region(region_id: str) -> dict[str, Any]:
    """Fetch GreenLake service managers available for a region mapping ID."""
    return _glp_read(f"/service-catalog/v1/per-region-service-managers/{_path_part(region_id)}")


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
