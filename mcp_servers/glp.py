"""MCP server — GreenLake Platform (GLP): inventory, licensing, users, and
service catalog (76 curated + 904 active generated tools; 918 in provenance manifest).

Covers: GLP device lifecycle (v1 + v2beta1), device grouping summaries,
subscription assignment/bulk-add, auto-subscription-setting reads/updates,
audit logs (v1 + v2beta1), users, workspaces (incl. contact PATCH), reporting
statuses, service-catalog reads, and a guarded read-only GLP GET. Curated
workflows also cover RBAC role-assignment and scope-group lifecycle
(create/update/delete), identity user lifecycle (invite/update-preferences/
disassociate), event webhooks/subscriptions/deliveries, workspace tags/
locations, and SCIM user/group membership reads (see list_glp_api_families).
Uses the target_account (glp_account) credentials.
"""
import asyncio
import os
import re
from typing import Any, Literal
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
    # Curated typed reads below cover the common workflows in these families;
    # glp_get remains available for documented resources without a named tool.
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
            "update_glp_workspace_contact",
            "glp_add_subscriptions",
            "create_glp_role_assignment",
            "update_glp_role_assignment",
            "delete_glp_role_assignment",
            "create_glp_scope_group",
            "update_glp_scope_group",
            "delete_glp_scope_group",
            "add_glp_scope_group_scopes",
            "delete_glp_scope_group_scopes",
            "invite_glp_user",
            "update_glp_user_preferences",
            "disassociate_glp_user",
            "update_glp_auto_subscription_settings",
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


def _glp_list_read(
    path: str,
    params: dict[str, Any],
    *,
    limit: int,
    list_key: str | None = "items",
) -> dict[str, Any]:
    result = _glp_read(path, params)
    if result.get("data") is not None:
        result["data"] = redact_sensitive(
            bound_collection_response(
                result["data"],
                limit=clamp_limit(limit),
                offset=0,
                list_key=list_key,
            )
        )
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
def list_glp_auto_subscription_settings() -> dict[str, Any]:
    """List all configured auto-subscription settings in the GLP workspace."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.list_auto_subscription_settings()
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


@mcp.tool(annotations=READ_ONLY)
def get_glp_auto_subscription_setting(setting_id: str) -> dict[str, Any]:
    """Fetch one configured auto-subscription setting by ID."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        setting = glp.get_auto_subscription_setting(setting_id)
        return {"setting": setting, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"setting": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_glp_auto_subscription_settings(
    setting_id: str, settings: dict[str, Any]
) -> dict[str, Any]:
    """PATCH the configured auto-subscription settings for a workspace.

    Pass a list of deviceType/tier combinations to create or update in
    `settings`; per the spec, pass `tier` as null for a deviceType to
    remove its auto-subscription setting. The manifest's declared body
    property and required property don't agree with each other for this
    operation (not independently re-verified against the Subscriptions v1
    spec text) — `settings` is sent through as-is, so treat a 400/422 as
    "shape not confirmed on this tenant" and inspect
    list_glp_auto_subscription_settings / get_glp_auto_subscription_setting
    first. Gated behind the same guardrail as other GLP v2beta1-style
    writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "update_glp_auto_subscription_settings",
        {"setting_id": setting_id, "settings": settings},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.update_auto_subscription_settings(setting_id, settings)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


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




@mcp.tool(annotations=IDEMPOTENT_WRITE)
def invite_glp_user(email: str, send_welcome_email: bool | None = None) -> dict[str, Any]:
    """Invite a user to the GLP workspace by email.

    Gated behind the same guardrail as other GLP v2beta1-style writes —
    see glp_write_status.
    """
    disabled = _write_disabled(
        "invite_glp_user",
        {"email": redact_sensitive(email), "send_welcome_email": send_welcome_email},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.invite_user(email, send_welcome_email=send_welcome_email)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_glp_user_preferences(
    user_id: str, idle_timeout: int, language: str
) -> dict[str, Any]:
    """Update a GLP user's preferences (idle timeout, language).

    This is a full PUT replace of the user's preferences — both
    `idle_timeout` and `language` are required. Gated behind the same
    guardrail as other GLP v2beta1-style writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "update_glp_user_preferences",
        {"user_id": user_id, "idle_timeout": idle_timeout, "language": language},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.update_user_preferences(user_id, idle_timeout, language)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
def disassociate_glp_user(user_id: str) -> dict[str, Any]:
    """Remove (disassociate) a user from the GLP workspace.

    Gated behind the same guardrail as other GLP v2beta1-style writes —
    see glp_write_status.
    """
    disabled = _write_disabled("disassociate_glp_user", {"user_id": user_id})
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.disassociate_user(user_id)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}
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


# ── Authorization / RBAC v1beta1 ─────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_role_assignments(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List RBAC role assignments with bounded offset pagination.

    The documented OData subset supports `in` and `and` on role, scope, and
    principal, with each attribute appearing at most once.
    """
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/authorization/v1beta1/role-assignments",
        _paged_params(bounded_limit, offset, filter=filter),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_role_assignment(role_assignment_id: str) -> dict[str, Any]:
    """Fetch one RBAC role assignment by ID."""
    return _glp_read(
        f"/authorization/v1beta1/role-assignments/{_path_part(role_assignment_id)}"
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_scope_groups(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """List RBAC scope groups with bounded offset pagination."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/authorization/v1beta1/scope-groups",
        _paged_params(bounded_limit, offset, filter=filter, sort=sort),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_scope_group(scope_group_id: str) -> dict[str, Any]:
    """Fetch one RBAC scope group by ID."""
    return _glp_read(
        f"/authorization/v1beta1/scope-groups/{_path_part(scope_group_id)}"
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_scope_group_scopes(
    scope_group_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List scopes assigned to an RBAC scope group."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        f"/authorization/v1beta1/scope-groups/{_path_part(scope_group_id)}/scopes",
        _paged_params(bounded_limit, offset),
        limit=bounded_limit,
    )



@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_glp_role_assignment(role_assignment: dict[str, Any]) -> dict[str, Any]:
    """Create an RBAC role assignment.

    `role_assignment` is passed through as-is; per the spec it must include
    `principal`, `role`, and `scope` (see get_glp_role_assignment /
    list_glp_role_assignments for the shape returned by this same API, and
    the GLP authorization developer guide for how to find those
    identifiers). Gated behind the same guardrail as other GLP v2beta1-style
    writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "create_glp_role_assignment", {"role_assignment": role_assignment}
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.create_role_assignment(role_assignment)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_glp_role_assignment(
    role_assignment_id: str, role_assignment: dict[str, Any]
) -> dict[str, Any]:
    """Update the scope(s) of an existing RBAC role assignment by ID.

    Per the spec, `role_assignment` must still include the immutable `id`,
    `principal`, and `role` attributes alongside the updated `scope`. Gated
    behind the same guardrail as other GLP v2beta1-style writes — see
    glp_write_status.
    """
    disabled = _write_disabled(
        "update_glp_role_assignment",
        {"role_assignment_id": role_assignment_id, "role_assignment": role_assignment},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.update_role_assignment(role_assignment_id, role_assignment)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_glp_role_assignment(role_assignment_id: str) -> dict[str, Any]:
    """Delete an RBAC role assignment by ID.

    Gated behind the same guardrail as other GLP v2beta1-style writes —
    see glp_write_status.
    """
    disabled = _write_disabled(
        "delete_glp_role_assignment", {"role_assignment_id": role_assignment_id}
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.delete_role_assignment(role_assignment_id)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_glp_scope_group(scope_group: dict[str, Any]) -> dict[str, Any]:
    """Create an RBAC scope group (a named collection of scopes for role assignments).

    `scope_group` is passed through as-is; per the spec it must include
    `name`, and a scope group cannot contain another scope group (no
    nesting). Gated behind the same guardrail as other GLP v2beta1-style
    writes — see glp_write_status.
    """
    disabled = _write_disabled("create_glp_scope_group", {"scope_group": scope_group})
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.create_scope_group(scope_group)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_glp_scope_group(
    scope_group_id: str, scope_group: dict[str, Any]
) -> dict[str, Any]:
    """Update an RBAC scope group by ID.

    Per the spec, `scope_group` must still include the immutable `id`
    attribute. Gated behind the same guardrail as other GLP v2beta1-style
    writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "update_glp_scope_group",
        {"scope_group_id": scope_group_id, "scope_group": scope_group},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.update_scope_group(scope_group_id, scope_group)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_glp_scope_group(scope_group_id: str) -> dict[str, Any]:
    """Delete an RBAC scope group by ID.

    Gated behind the same guardrail as other GLP v2beta1-style writes —
    see glp_write_status.
    """
    disabled = _write_disabled(
        "delete_glp_scope_group", {"scope_group_id": scope_group_id}
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.delete_scope_group(scope_group_id)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def add_glp_scope_group_scopes(
    scope_group_id: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Add scopes to an existing RBAC scope group.

    `items` is required by the spec. This operation is synchronous and
    non-atomic per the spec. Gated behind the same guardrail as other GLP
    v2beta1-style writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "add_glp_scope_group_scopes",
        {"scope_group_id": scope_group_id, "items": items},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.add_scope_group_scopes(scope_group_id, items)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}


@mcp.tool(annotations=DESTRUCTIVE)
def delete_glp_scope_group_scopes(
    scope_group_id: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Delete scopes from an existing RBAC scope group.

    `items` is required by the spec (the scope IDs to remove — see
    list_glp_scope_group_scopes to find them). This operation is
    synchronous and non-atomic per the spec. Gated behind the same
    guardrail as other GLP v2beta1-style writes — see glp_write_status.
    """
    disabled = _write_disabled(
        "delete_glp_scope_group_scopes",
        {"scope_group_id": scope_group_id, "items": items},
    )
    if disabled:
        return disabled
    glp = get_glp_client()
    errors: list[str] = []
    try:
        result = glp.delete_scope_group_scopes(scope_group_id, items)
        return {"result": result, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"result": None, "errors": errors}

# ── Event webhooks v1beta1 ───────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_event_webhooks(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List workspace event webhooks, newest first."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/events/v1beta1/webhooks",
        _paged_params(bounded_limit, offset),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_event_webhook(webhook_id: str) -> dict[str, Any]:
    """Fetch one workspace event webhook by ID."""
    return _glp_read(f"/events/v1beta1/webhooks/{_path_part(webhook_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_event_subscriptions(
    filter: str,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """List event subscriptions for a webhook.

    `filter` is required by the GLP v1beta1 operation; the documented
    supported filter field is `webhookId`.
    """
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/events/v1beta1/subscriptions",
        _paged_params(bounded_limit, offset, filter=filter),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_webhook_deliveries(
    webhook_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List recent delivery attempts for a workspace event webhook."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        f"/events/v1beta1/webhooks/{_path_part(webhook_id)}/recent-deliveries",
        _paged_params(bounded_limit, offset),
        limit=bounded_limit,
    )


# ── Location management v1 ───────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_locations(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """List workspace locations; the documented filter supports location name."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/locations/v1/locations",
        _paged_params(bounded_limit, offset, filter=filter),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_location(location_id: str) -> dict[str, Any]:
    """Fetch one workspace location by ID."""
    return _glp_read(f"/locations/v1/locations/{_path_part(location_id)}")


@mcp.tool(annotations=READ_ONLY)
def reverse_geocode_glp_location(
    latitude: float,
    longitude: float,
    language: str | None = None,
) -> dict[str, Any]:
    """Resolve latitude/longitude to a location, optionally using an ISO language code."""
    return _glp_read(
        "/locations/v1/locations/address/revgeocode",
        _params(latitude=latitude, longitude=longitude, language=language),
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_location_tags(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List location-management tags for the workspace."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/locations/v1/locations/tags",
        _paged_params(bounded_limit, offset),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_location_tags(location_id: str) -> dict[str, Any]:
    """Fetch location-management tags assigned to one location."""
    return _glp_read(f"/locations/v1/locations/tags/{_path_part(location_id)}")


# ── Workspace tags v1 ────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_tags(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    sort: str | None = None,
    select: list[str] | None = None,
) -> dict[str, Any]:
    """List workspace tags with filter, sort, projection, and bounded pagination."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/tags/v1/tags",
        _paged_params(
            bounded_limit,
            offset,
            filter=filter,
            sort=sort,
            select=select,
        ),
        limit=bounded_limit,
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_tag_resources(
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
    filter_tags: str | None = None,
    sort: str | None = None,
    select: list[str] | None = None,
) -> dict[str, Any]:
    """List tagged workspace resources with bounded pagination."""
    bounded_limit = clamp_limit(limit)
    return _glp_list_read(
        "/tags/v1/tag-resources",
        _paged_params(
            bounded_limit,
            offset,
            filter=filter,
            sort=sort,
            select=select,
            **{"filter-tags": filter_tags},
        ),
        limit=bounded_limit,
    )


# ── Identity SCIM v2beta1 ────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
def list_glp_scim_users(
    filter: str | None = None,
    count: int = 100,
    start_index: int = 1,
    sort_by: Literal["displayName", "meta.lastLogin"] | None = None,
    sort_order: Literal["ascending", "descending"] | None = None,
) -> dict[str, Any]:
    """List SCIM users with 1-based pagination.

    Supported user filters are displayName/userName with sw, eq, or co.
    sort_by supports displayName or meta.lastLogin; sort_order supports
    ascending or descending.
    """
    bounded_count = clamp_limit(count)
    return _glp_list_read(
        "/identity/v2beta1/scim/v2/Users",
        _params(
            filter=filter,
            count=bounded_count,
            startIndex=max(1, start_index),
            sortBy=sort_by,
            sortOrder=sort_order,
        ),
        limit=bounded_count,
        list_key="Resources",
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_scim_user(user_id: str) -> dict[str, Any]:
    """Fetch one SCIM user by ID."""
    return _glp_read(f"/identity/v2beta1/scim/v2/Users/{_path_part(user_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_scim_groups(
    filter: str | None = None,
    count: int = 100,
    start_index: int = 1,
) -> dict[str, Any]:
    """List SCIM user groups with 1-based pagination."""
    bounded_count = clamp_limit(count)
    return _glp_list_read(
        "/identity/v2beta1/scim/v2/Groups",
        _params(
            filter=filter,
            count=bounded_count,
            startIndex=max(1, start_index),
        ),
        limit=bounded_count,
        list_key="Resources",
    )


@mcp.tool(annotations=READ_ONLY)
def get_glp_scim_group(group_id: str) -> dict[str, Any]:
    """Fetch one SCIM user group by ID."""
    return _glp_read(f"/identity/v2beta1/scim/v2/Groups/{_path_part(group_id)}")


@mcp.tool(annotations=READ_ONLY)
def list_glp_scim_group_users(
    group_id: str,
    count: int = 100,
    start_index: int = 1,
) -> dict[str, Any]:
    """List SCIM users assigned to a user group."""
    bounded_count = clamp_limit(count)
    return _glp_list_read(
        f"/identity/v2beta1/scim/v2/extensions/Groups/{_path_part(group_id)}/users",
        _params(count=bounded_count, startIndex=max(1, start_index)),
        limit=bounded_count,
        list_key="Resources",
    )


@mcp.tool(annotations=READ_ONLY)
def list_glp_scim_user_groups(
    user_id: str,
    count: int = 100,
    start_index: int = 1,
) -> dict[str, Any]:
    """List SCIM groups assigned to a user."""
    bounded_count = clamp_limit(count)
    return _glp_list_read(
        f"/identity/v2beta1/scim/v2/extensions/Users/{_path_part(user_id)}/groups",
        _params(count=bounded_count, startIndex=max(1, start_index)),
        limit=bounded_count,
        list_key="Resources",
    )


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
def group_glp_devices(
    group_by: str,
    limit: int = 100,
    offset: int = 0,
    filter: str | None = None,
) -> dict[str, Any]:
    """Group GLP devices by a documented v2beta1 attribute."""
    glp = get_glp_client()
    errors: list[str] = []
    try:
        items = glp.group_devices_v2beta1(
            group_by=group_by,
            limit=clamp_limit(limit),
            offset=max(0, offset),
            filter=filter,
        )
        return {"items": items, "errors": errors}
    except Exception as exc:
        errors.append(str(exc))
        return {"items": [], "errors": errors}


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

    dry_run=True sends the request with the manifest-confirmed ``dry-run``
    query parameter (postSubscriptionsV1; validation only — no subscriptions
    are actually added), rather than a purely local no-op. The nested
    subscription-item body shape has not been independently re-verified
    against live spec text — treat a 400/404 here as "not confirmed on this
    tenant" and fall back to glp_get for exploration. Gated behind the same
    guardrail as other GLP v2beta1-style writes — see glp_write_status.
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
    dedicated typed tools are manifest-backed vs. best-effort/unconfirmed.
    """
    manifest_backed_tools = [
        "list_glp_devices", "get_glp_device", "get_glp_device_by_id",
        "list_glp_devices_v2", "get_glp_device_v2",
        "list_glp_subscriptions", "get_glp_subscription",
        "list_glp_auto_subscription_settings", "get_glp_auto_subscription_setting",
        "update_glp_auto_subscription_settings",
        "list_glp_users", "get_glp_user",
        "invite_glp_user", "update_glp_user_preferences", "disassociate_glp_user",
        "list_glp_audit_logs", "get_glp_audit_log_detail",
        "list_glp_audit_logs_v2", "get_glp_audit_log_v2", "get_glp_audit_log_v2_detail",
        "get_glp_workspace", "get_glp_workspace_contact", "update_glp_workspace_contact",
        "list_glp_reporting_statuses", "get_glp_reporting_status",
        "list_glp_service_offers", "get_glp_service_offer",
        "list_glp_role_assignments", "get_glp_role_assignment",
        "create_glp_role_assignment", "update_glp_role_assignment", "delete_glp_role_assignment",
        "list_glp_scope_groups", "get_glp_scope_group", "list_glp_scope_group_scopes",
        "create_glp_scope_group", "update_glp_scope_group", "delete_glp_scope_group",
        "add_glp_scope_group_scopes", "delete_glp_scope_group_scopes",
        "list_glp_event_webhooks", "get_glp_event_webhook",
        "list_glp_event_subscriptions", "list_glp_webhook_deliveries",
        "list_glp_locations", "get_glp_location", "reverse_geocode_glp_location",
        "list_glp_location_tags", "get_glp_location_tags",
        "list_glp_tags", "list_glp_tag_resources",
        "list_glp_scim_users", "get_glp_scim_user",
        "list_glp_scim_groups", "get_glp_scim_group",
        "list_glp_scim_group_users", "list_glp_scim_user_groups",
    ]
    return {
        "guarded_get_prefixes": list(_GLP_GET_PREFIXES),
        "confirmed_typed_tools": manifest_backed_tools,
        "curated_manifest_backed_tools": manifest_backed_tools,
        "best_effort_typed_tools": [
            "group_glp_devices",
            "glp_add_subscriptions",
        ],
        "explore_only_families": {
            "notifications": "/notifications/...",
            "API client credentials": "no confirmed path — not exposed via glp_get yet",
        },
        "note": (
            "Named RBAC, event-webhook, tag, location, and SCIM reads are backed by "
            "the committed GLP OpenAPI manifest. RBAC role-assignment/scope-group "
            "lifecycle, identity user lifecycle, and auto-subscription-setting writes "
            "are also manifest-backed and gated behind glp_write_status. Use glp_get "
            "only for other documented resources under an allowed prefix."
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
# by CENTRALMCP_GLP_GENERATED_TOOLS and defaults OFF (see
# _glp_generated_enabled below) except in `direct` router mode with the
# `glp`/`all` toolset, so the default curated aruba-glp catalog stays small.
#
# The 76 curated GLP tools above are the confirmed-working, hand-tuned surface;
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
_GLP_GENERATED_ROUTES: list[tuple[re.Pattern[str], tuple[str, ...]]] = []
_GLP_SUNSET_OPERATION_PREFIXES = (
    "/devices/v1beta1/",
    "/subscriptions/v1alpha1/",
    "/subscriptions/v1beta1/",
)

_GLP_GENERATED_EXECUTE_HINT = (
    "Re-run with dry_run=False and confirm=True to execute this GLP write "
    f"(requires {_V2BETA1_WRITES_FLAG}=1)."
)


def _glp_generated_prefixes() -> tuple[str, ...]:
    return _GLP_GENERATED_PREFIXES


def _glp_route_pattern(path_template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", path_template)
    pattern = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{pattern}$")


def _glp_generated_server(path: str, configured_base_url: str) -> str:
    server_urls: tuple[str, ...] = ()
    for pattern, candidates in _GLP_GENERATED_ROUTES:
        if pattern.fullmatch(path):
            server_urls = candidates
            break
    if not server_urls:
        return configured_base_url.rstrip("/")
    configured = configured_base_url.rstrip("/")
    if configured in server_urls:
        return configured
    global_url = "https://global.api.greenlake.hpe.com"
    if global_url in server_urls:
        return global_url
    if len(server_urls) == 1:
        return server_urls[0]

    region = os.environ.get("GLP_GENERATED_REGION", "").strip().lower()
    regional_hosts = {
        "us-west": "https://us-west.api.greenlake.hpe.com",
        "eu-west": "https://eu-west.api.greenlake.hpe.com",
        "eu-central": "https://eu-central.api.greenlake.hpe.com",
        "ap-northeast": "https://ap-northeast.api.greenlake.hpe.com",
    }
    data_hosts = {
        "us-west": "https://us1.data.cloud.hpe.com",
        "us1": "https://us1.data.cloud.hpe.com",
        "eu-west": "https://eu1.data.cloud.hpe.com",
        "eu-central": "https://eu1.data.cloud.hpe.com",
        "eu1": "https://eu1.data.cloud.hpe.com",
        "ap-northeast": "https://jp1.data.cloud.hpe.com",
        "jp1": "https://jp1.data.cloud.hpe.com",
    }
    requested = (
        data_hosts.get(region)
        if any(".data.cloud.hpe.com" in url for url in server_urls)
        else regional_hosts.get(region)
    )
    if requested in server_urls:
        return requested
    raise ValueError(
        "This generated GLP operation is region-specific. Set GLP_GENERATED_REGION "
        "to one of us-west, eu-west, eu-central, or ap-northeast."
    )


async def _glp_generated_auth_headers(
    path: str | dict[str, str] | None,
    extra: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(base_url, headers)`` with trusted GLP auth injected last.

    Reuses the target-account GLPClient's underlying token manager (its
    workspace-scoped bearer token) and GLP base URL, injecting the
    Authorization header last. Non-auth header params are preserved; the
    client's httpx session is never touched here.
    """
    if not isinstance(path, str):
        extra = path
        path = ""
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
    base_url = (
        _glp_generated_server(path, client.base_url)
        if path
        else client.base_url.rstrip("/")
    )
    return base_url, headers


async def _glp_generated_refresh_auth() -> None:
    client = get_glp_client()._client
    await asyncio.to_thread(client.token_manager.get_access_token, True)


def _glp_generated_enabled() -> bool:
    """Whether the generated GLP tools should register.

    Opt-in and **default OFF**: unlike the optional-product starter backends,
    the ~918 generated GLP tools are a very large surface, so we keep the
    default ``aruba-glp`` catalog to the 76 curated tools and only expand when
    an operator sets ``CENTRALMCP_GLP_GENERATED_TOOLS`` truthy. (Central's
    generated tools live on a separate ``aruba-central-generated`` server, so
    they can default on without inflating a shared catalog; the GLP generated
    tools share the curated ``aruba-glp`` server, hence the opt-in default.)
    """
    raw = os.environ.get("CENTRALMCP_GLP_GENERATED_TOOLS")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    router_mode = os.environ.get("CENTRALMCP_ROUTER_MODE", "").strip().lower()
    toolsets = {
        item.strip().lower()
        for item in os.environ.get("CENTRALMCP_TOOLSETS", "").split(",")
        if item.strip()
    }
    return router_mode == "direct" and bool({"glp", "all"} & toolsets)


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
    active_manifest = {
        **manifest,
        "operations": [
            operation
            for operation in manifest.get("operations", [])
            if not operation["path"].startswith(_GLP_SUNSET_OPERATION_PREFIXES)
        ],
    }
    global _GLP_GENERATED_PREFIXES, _GLP_GENERATED_ROUTES
    prefixes = sorted(
        {
            "/" + op["path"].split("/", 2)[1] + "/"
            for op in manifest.get("operations", [])
            if isinstance(op.get("path"), str) and op["path"].startswith("/")
        }
    )
    if prefixes:
        _GLP_GENERATED_PREFIXES = tuple(prefixes)
    _GLP_GENERATED_ROUTES = [
        (
            _glp_route_pattern(operation["path"]),
            tuple(operation.get("server_urls") or ()),
        )
        for operation in active_manifest.get("operations", [])
    ]

    read_executor = make_read_executor(
        resolve=_glp_generated_auth_headers,
        allowed_prefixes=_glp_generated_prefixes,
        not_configured="GLP not configured",
        refresh_auth=_glp_generated_refresh_auth,
    )
    write_executor = make_write_executor(
        resolve=_glp_generated_auth_headers,
        allowed_prefixes=_glp_generated_prefixes,
        writes_allowed=lambda: platform_writes_allowed("glp"),
        blocked_response=lambda name: platform_write_blocked("glp", name),
        execute_hint=_GLP_GENERATED_EXECUTE_HINT,
        not_configured="GLP not configured",
        refresh_auth=_glp_generated_refresh_auth,
    )
    GENERATED_GLP_TOOLS = register_generated_tools(
        mcp,
        "glp",
        read_executor=read_executor,
        write_executor=write_executor,
        manifest=active_manifest,
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
