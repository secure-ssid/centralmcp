"""MCP server — GreenLake Platform (GLP): inventory, licensing, users, and
service catalog (41 curated + 918 generated OpenAPI tools).

Covers: GLP device lifecycle (v1 + v2beta1), device groups (v2beta1, best-effort),
subscription assignment/bulk-add, audit logs (v1 + v2beta1), users, workspaces
(incl. contact PATCH), reporting statuses, service-catalog reads, and a guarded
read-only GLP GET covering RBAC/authorization, events, webhooks, tags, location,
and SCIM families pending dedicated typed wrappers (see list_glp_api_families).
Uses the target_account (glp_account) credentials.
"""
import asyncio
import os
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
    platform_write_blocked,
    platform_writes_allowed,
    redact_sensitive,
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
    # Added for RBAC/authorization, events/webhooks, tags, location, and
    # SCIM reads. Exact resource shapes for these families have not been
    # independently re-verified against live spec text (unlike the
    # devices/subscriptions/audit-log/workspaces families above, which back
    # confirmed-working typed tools) — use glp_get to explore before adding
    # dedicated typed wrappers. See glp_write_status / list_glp_api_families
    # for what remains unconfirmed.
    "/authorization/",
    "/events/",
    "/webhooks/",
    "/notifications/",
    "/tags/",
    "/locations/",
    "/scim/",
)
_SENSITIVE_QUERY_PARAMS = {"unredacted"}


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


def _safe_read_params(params: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    safe_params = dict(params or {})
    removed = [
        key
        for key in list(safe_params)
        if str(key).strip().lower() in _SENSITIVE_QUERY_PARAMS
    ]
    for key in removed:
        safe_params.pop(key, None)

    warnings = []
    if removed:
        warnings.append(
            "GLP unredacted responses are disabled; removed unredacted query parameter."
        )
    return safe_params, warnings


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
    safe_params, warnings = _safe_read_params(params)
    try:
        safe_api_path(path, _GLP_GET_PREFIXES)
    except ValueError as exc:
        return {"data": None, "endpoint_used": path, "errors": [f"Invalid path. {exc}"]}
    try:
        client = get_glp_client()._client
        if headers:
            response = client._request("GET", path, params=safe_params, headers=headers)
            response.raise_for_status()
            data = response.json()
        else:
            data = client.get(path, params=safe_params)
        result = {"data": redact_sensitive(data), "endpoint_used": path, "errors": []}
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as exc:
        result = {"data": None, "endpoint_used": path, "errors": [str(exc)]}
        if warnings:
            result["warnings"] = warnings
        return result


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
    safe_params, warnings = _safe_read_params(params)
    try:
        data = get_glp_client()._client.get(safe_path, params=safe_params)
        data = redact_sensitive(bound_collection_response(data, limit=limit, offset=offset))
        result = {"data": data, "endpoint_used": safe_path}
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as exc:
        result = {"error": str(exc), "endpoint_used": safe_path}
        if warnings:
            result["warnings"] = warnings
        return result


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
            all=all_workspaces,
        ),
        headers=headers,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_service_provision(
    provision_id: str,
) -> dict[str, Any]:
    """Fetch a GreenLake service provision by ID."""
    return _glp_read(
        f"/service-catalog/v1beta1/service-provisions/{_path_part(provision_id)}",
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


# ── Devices v2beta1 / Device Groups v2beta1 ──────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_devices_v2(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List devices via the GLP Devices v2beta1 collection.

    Prefer this over list_glp_devices when you need v2beta1-only fields
    (e.g. the fields exposed by the v2beta1 PATCH path used for archive /
    subscription-assign). Falls back with a clear error if v2beta1 isn't
    available on this tenant yet — use list_glp_devices (v1) instead.
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_devices_v2beta1(
            limit=clamp_limit(limit), offset=max(0, offset), filter=filter
        )
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_device_v2(device_id: str) -> dict[str, Any]:
    """Fetch a single device via the GLP Devices v2beta1 collection by GLP device ID."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        device = glp.get_device_v2beta1(device_id)
        return {"device": device, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"device": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def list_glp_device_groups(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List device groups via the GLP Devices v2beta1 service.

    Endpoint path is inferred from the sibling /devices/v2beta1/devices
    collection convention and has not been independently re-verified
    against live spec text — a 404 here means "not confirmed on this
    tenant," not a client bug. Use glp_get("/devices/...") to probe
    alternate paths if this 404s.
    """
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_device_groups_v2beta1(
            limit=clamp_limit(limit), offset=max(0, offset), filter=filter
        )
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_device_group(group_id: str) -> dict[str, Any]:
    """Fetch a single device group via the GLP Devices v2beta1 service by ID."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        group = glp.get_device_group_v2beta1(group_id)
        return {"device_group": group, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"device_group": None, "errors": errors}


# ── Audit Logs v2beta1 ────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_audit_logs_v2(
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
) -> dict[str, Any]:
    """List GLP audit log entries via the v2beta1 Audit Log service."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_audit_logs_v2beta1(
            limit=clamp_limit(limit), offset=max(0, offset), category=category
        )
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_audit_log_v2(audit_log_id: str) -> dict[str, Any]:
    """Fetch a single GLP audit-log entry by ID via the v2beta1 Audit Log service."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        entry = glp.get_audit_log_v2beta1(audit_log_id)
        return {"audit_log": entry, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"audit_log": None, "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_audit_log_v2_detail(audit_log_id: str) -> dict[str, Any]:
    """Fetch full detail for a v2beta1 GLP audit-log entry (entries with details enabled)."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        detail = glp.get_audit_log_v2beta1_detail(audit_log_id)
        return {"audit_log_detail": detail, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"audit_log_detail": None, "errors": errors}


# ── Workspace contact/location PATCH, subscription bulk-add ─────────────────

@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_glp_workspace_contact(workspace_id: str, contact: dict[str, Any]) -> dict[str, Any]:
    """PATCH the contact record for a GLP workspace.

    Endpoint mirrors the confirmed-working GET at the same path
    (get_glp_workspace_contact). Gated behind the same guardrail as the
    device v2beta1 writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "update_glp_workspace_contact",
        {"workspace_id": workspace_id, "contact": redact_sensitive(contact)},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.update_workspace_contact(workspace_id, contact)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def glp_add_subscriptions(subscription_keys: list[str], dry_run: bool = False) -> dict[str, Any]:
    """Add one or more subscription keys to the GLP workspace, with an optional dry-run preview.

    dry_run=True sends the request with a server-side dryRun flag when the
    tenant supports it (validation only — no subscriptions are actually
    added), rather than a purely local no-op. Body/param shape has not been
    independently re-verified against live spec text — treat a 400/404 here
    as "not confirmed on this tenant" and fall back to glp_get for
    exploration. Gated behind the same guardrail as other GLP v2beta1-style
    writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "glp_add_subscriptions",
        {"subscription_keys": subscription_keys, "dry_run": dry_run},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.add_subscriptions(subscription_keys, dry_run=dry_run)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


# ── API family discovery ──────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_api_families() -> dict[str, Any]:
    """List guarded GLP GET path-prefixes reachable via glp_get, and note which
    dedicated typed tools are confirmed-working vs. best-effort/unconfirmed.

    Use this before assuming a typed wrapper exists for RBAC/authorization,
    events, webhooks, tags, locations, SCIM, or API-client credentials —
    those families are reachable through glp_get for exploration but do not
    yet have dedicated typed wrappers pending live-tenant/spec verification.
    """
    return {
        "guarded_get_prefixes": list(_GLP_GET_PREFIXES),
        "confirmed_typed_tools": [
            "list_glp_devices", "get_glp_device", "get_glp_device_by_id",
            "list_glp_devices_v2", "get_glp_device_v2",
            "list_glp_subscriptions", "get_glp_subscription",
            "list_glp_users", "get_glp_user",
            "list_glp_audit_logs", "get_glp_audit_log_detail",
            "list_glp_audit_logs_v2", "get_glp_audit_log_v2", "get_glp_audit_log_v2_detail",
            "get_glp_workspace", "get_glp_workspace_contact", "update_glp_workspace_contact",
            "list_glp_reporting_statuses", "get_glp_reporting_status",
            "list_glp_service_offers", "get_glp_service_offer",
        ],
        "best_effort_typed_tools": [
            "list_glp_device_groups", "get_glp_device_group",
            "glp_add_subscriptions",
        ],
        "explore_only_families": {
            "RBAC/authorization": "/authorization/...",
            "events": "/events/...",
            "webhooks": "/webhooks/...",
            "notifications": "/notifications/...",
            "tags": "/tags/...",
            "location": "/locations/...",
            "SCIM": "/scim/...",
            "API client credentials": "no confirmed path — not exposed via glp_get yet",
        },
        "note": (
            "explore_only_families have no dedicated typed wrapper in this pass — "
            "call glp_get(path=...) against the listed prefix to probe the exact "
            "resource shape on your tenant, then request a typed wrapper once confirmed."
        ),
    }



# ---------------------------------------------------------------------------
# Generated GreenLake (GLP) tools (see mcp_servers/openapi_gen). The committed
# manifest at mcp_servers/openapi_gen/manifests/glp.json is derived from the
# MIT-licensed nowireless4u/hpe-networking-mcp project's vendored HPE GreenLake
# OpenAPI specs (raw specs are proprietary and NOT committed — see the manifest
# "provenance" block and scripts/generate_glp_tools.py). Every unique documented
# GLP operation becomes a directly-callable, typed FastMCP tool that reuses the
# target-account GLPClient auth/workspace/retry behavior. Registration is guarded
# by CENTRALMCP_GLP_GENERATED_TOOLS and defaults ON when the manifest exists.
#
# The 41 curated GLP tools above are the confirmed-working, hand-tuned surface;
# the generated glp_* tools broaden coverage to the full workspace/inventory/
# licensing/service-catalog/storage/compute surface. Generated writes stay
# fail-closed behind the same CENTRALMCP_GLP_V2BETA1_WRITES gate and default to
# dry_run=True.
# ---------------------------------------------------------------------------

# Auth header/cookie names never forwarded from model-supplied header params;
# trusted GLP auth is injected last by _glp_generated_auth_headers.
_GLP_AUTH_HEADER_NAMES = {"authorization", "cookie"}

# Populated at registration time from the committed manifest (defense-in-depth
# path allow-list). The shared runtime already URL-escapes path values and
# rejects traversal segments, so this is belt-and-suspenders.
_GLP_GENERATED_PREFIXES: tuple[str, ...] = ("/devices/", "/subscriptions/", "/workspaces/")

_GLP_GENERATED_EXECUTE_HINT = (
    "Re-run with dry_run=False and confirm=True to execute this GLP write "
    f"(requires {_V2BETA1_WRITES_FLAG}=1)."
)


def _glp_generated_prefixes() -> tuple[str, ...]:
    return _GLP_GENERATED_PREFIXES


async def _glp_generated_auth_headers(extra: dict[str, str] | None) -> tuple[str, dict[str, str]]:
    """Return ``(base_url, headers)`` with trusted GLP auth injected last.

    Reuses the target-account GLPClient's underlying token manager (its
    workspace-scoped bearer token) and GLP base URL, injecting the
    Authorization header last. Non-auth header params are preserved; the
    client's httpx session is never touched here.
    """
    client = get_glp_client()._client
    # Acquire the workspace-scoped GLP bearer token off the event loop via the
    # GLPClient's underlying token manager; never touch the client's httpx
    # session (that boundary is owned exclusively by CentralClient).
    token = await asyncio.to_thread(client.token_manager.get_access_token)
    headers: dict[str, str] = {"Accept": "application/json"}
    for key, value in (extra or {}).items():
        if key.strip().lower() in _GLP_AUTH_HEADER_NAMES:
            continue
        headers[key] = str(value)
    headers["Authorization"] = f"Bearer {token}"  # trusted auth injected last
    return client.base_url, headers


def _glp_generated_enabled() -> bool:
    """Whether the generated GLP tools should register.

    Opt-in and **default OFF**: unlike the optional-product starter backends,
    the ~918 generated GLP tools are a very large surface, so we keep the
    default ``aruba-glp`` catalog to the 41 curated tools and only expand when
    an operator sets ``CENTRALMCP_GLP_GENERATED_TOOLS`` truthy. (Central's
    generated tools live on a separate ``aruba-central-generated`` server, so
    they can default on without inflating a shared catalog; the GLP generated
    tools share the curated ``aruba-glp`` server, hence the opt-in default.)
    """
    raw = os.environ.get("CENTRALMCP_GLP_GENERATED_TOOLS")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _register_generated_glp_tools() -> list[str]:
    """Register generated GLP tools (idempotent).

    No-op (returns ``[]``) when the opt-in flag is off or the manifest is
    missing, so a stripped checkout never breaks import. Safe to call again
    after enabling the flag (e.g. from tests); already-registered tools are
    returned as-is.
    """
    global GENERATED_GLP_TOOLS
    if GENERATED_GLP_TOOLS:
        return GENERATED_GLP_TOOLS
    from mcp_servers.openapi_gen.http_exec import make_read_executor, make_write_executor
    from mcp_servers.openapi_gen.manifest import load_manifest, manifest_exists
    from mcp_servers.openapi_gen.runtime import register_generated_tools

    if not _glp_generated_enabled() or not manifest_exists("glp"):
        return []
    manifest = load_manifest("glp")
    global _GLP_GENERATED_PREFIXES
    prefixes = sorted(
        {
            "/" + op["path"].split("/", 2)[1] + "/"
            for op in manifest.get("operations", [])
            if isinstance(op.get("path"), str) and op["path"].startswith("/")
        }
    )
    if prefixes:
        _GLP_GENERATED_PREFIXES = tuple(prefixes)

    read_executor = make_read_executor(
        resolve=_glp_generated_auth_headers,
        allowed_prefixes=_glp_generated_prefixes,
        not_configured="GLP not configured",
    )
    write_executor = make_write_executor(
        resolve=_glp_generated_auth_headers,
        allowed_prefixes=_glp_generated_prefixes,
        writes_allowed=lambda: platform_writes_allowed("glp"),
        blocked_response=lambda name: platform_write_blocked("glp", name),
        execute_hint=_GLP_GENERATED_EXECUTE_HINT,
        not_configured="GLP not configured",
    )
    GENERATED_GLP_TOOLS = register_generated_tools(
        mcp,
        "glp",
        read_executor=read_executor,
        write_executor=write_executor,
        manifest=manifest,
    )
    return GENERATED_GLP_TOOLS


# Module global, populated by _register_generated_glp_tools(). Declared before
# the (opt-in) registration call so the idempotency guard has a value to read.
GENERATED_GLP_TOOLS: list[str] = []
GENERATED_GLP_TOOLS = _register_generated_glp_tools()


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        SecretTokenizeMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(
        mcp,
        [
            NullStripMiddleware(),
            RateLimitMiddleware(rate=8.0),
            SecretTokenizeMiddleware(),
        ],
    )
    from mcp_servers.shared import run_server
    run_server(mcp)
