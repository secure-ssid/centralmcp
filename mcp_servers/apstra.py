"""MCP server — optional HPE Juniper Apstra backend (low-surface starter tools).

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=apstra

Auth/env:
  APSTRA_BASE_URL   e.g. https://apstra.example.com
  APSTRA_API_TOKEN  static bearer token
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    READ_ONLY,
    bound_collection_response,
    optional_product_write_blocked,
    optional_product_writes_allowed,
    redact_sensitive,
    response_payload,
    safe_api_path,
    validate_product_base_url,
)

mcp = FastMCP("apstra-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."
_BLUEPRINT_FIELDS = (
    "id",
    "label",
    "name",
    "status",
    "state",
    "role",
    "version",
    "design",
    "reference_design",
)
_TEMPLATE_FIELDS = (
    "id",
    "label",
    "name",
    "display_name",
    "type",
    "design",
    "reference_design",
    "version",
    "description",
    "tags",
)
_ANOMALY_FIELDS = (
    "id",
    "identity",
    "type",
    "severity",
    "role",
    "count",
    "description",
    "acknowledged",
)
_RACK_FIELDS = (
    "id",
    "label",
    "name",
    "description",
    "rack_type",
    "template_name",
    "fabric_connectivity_design",
    "leaf_count",
    "spine_count",
    "systems_count",
    "tags",
)
_ROUTING_ZONE_FIELDS = (
    "id",
    "label",
    "name",
    "description",
    "vni",
    "vlan_id",
    "vrf_name",
    "sz_type",
    "routing_policy",
    "rt_policy",
)
_VIRTUAL_NETWORK_FIELDS = (
    "id",
    "label",
    "name",
    "vn_type",
    "security_zone_id",
    "virtual_gateway_ipv4",
    "ipv4_subnet",
    "virtual_gateway_ipv6",
    "ipv6_subnet",
    "vni",
    "vni_id",
    "reserved_vlan_id",
    "dhcp_service",
    "bound_to",
)
_REMOTE_GATEWAY_FIELDS = (
    "id",
    "label",
    "name",
    "gw_name",
    "gw_ip",
    "gw_asn",
    "local_gw_nodes",
    "evpn_route_types",
    "evpn_interconnect_group_id",
    "status",
    "state",
)
_CONNECTIVITY_TEMPLATE_FIELDS = (
    "id",
    "label",
    "name",
    "description",
    "policy_type",
    "visible",
    "used",
    "assigned",
    "tags",
    "status",
    "state",
)
_APPLICATION_ENDPOINT_FIELDS = (
    "id",
    "label",
    "name",
    "system_id",
    "system_label",
    "node_id",
    "interface_id",
    "interface_name",
    "if_name",
    "port_channel_id",
    "lag_id",
    "policy_id",
    "policy_label",
    "assigned",
    "tags",
)
_DIFF_STATUS_FIELDS = (
    "status",
    "state",
    "staging_version",
    "active_version",
    "deployed",
    "has_uncommitted_changes",
    "uncommitted_changes",
    "diff_summary",
    "warnings",
    "errors",
)
_PROTOCOL_SESSION_FIELDS = (
    "id",
    "label",
    "name",
    "protocol",
    "role",
    "local_system_id",
    "remote_system_id",
    "local_asn",
    "remote_asn",
    "local_ip",
    "remote_ip",
    "status",
    "state",
    "established",
    "last_change",
)
_SYSTEM_FIELDS = (
    "id",
    "system_id",
    "label",
    "name",
    "hostname",
    "role",
    "system_type",
    "device_key",
    "status",
    "state",
    "deploy_status",
    "management_ip",
    "ip_address",
    "asn",
    "model",
    "serial_number",
)


def _apstra_config() -> tuple[str | None, str | None]:
    import os

    base_url = os.getenv("APSTRA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("APSTRA_API_TOKEN", "").strip()
    return (base_url or None, token or None)


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _compact_record(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return item
    compacted = {key: item[key] for key in fields if key in item}
    return compacted or item


def _compact_collection(data: Any, fields: tuple[str, ...], list_keys: tuple[str, ...] = ()) -> Any:
    if isinstance(data, list):
        return [_compact_record(item, fields) for item in data]
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in (*list_keys, "items", "results", "data"):
        if isinstance(out.get(key), list):
            out[key] = [_compact_record(item, fields) for item in out[key]]
            break
    return out


@mcp.tool(annotations=READ_ONLY)
def apstra_status() -> dict[str, Any]:
    """Report whether Apstra backend is configured."""
    base_url, token = _apstra_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
    }


@mcp.tool(annotations=READ_ONLY)
async def apstra_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to Apstra API.

    Safety guard: only allows paths beginning with `/api/`.
    List payloads are bounded with `limit` and `offset`.
    """
    base_url, token = _apstra_config()
    if not base_url or not token:
        return {"error": "Apstra not configured. Set APSTRA_BASE_URL and APSTRA_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="Apstra")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


async def _apstra_read_post(
    path: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """POST to a read-only Apstra endpoint with a fixed, typed wrapper."""
    base_url, token = _apstra_config()
    if not base_url or not token:
        return {"error": "Apstra not configured. Set APSTRA_BASE_URL and APSTRA_API_TOKEN."}
    try:
        path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="Apstra")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers)
        payload = bound_collection_response(response_payload(resp), limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_blueprints(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List Apstra blueprints with compact ID/name/status fields."""
    out = await apstra_get("/api/blueprints", limit=limit, offset=offset)
    if "data" in out:
        out["blueprints"] = _compact_collection(out.pop("data"), _BLUEPRINT_FIELDS)
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_templates(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List Apstra design templates available for blueprint creation."""
    out = await apstra_get("/api/design/templates", limit=limit, offset=offset)
    if "data" in out:
        out["templates"] = _compact_collection(out.pop("data"), _TEMPLATE_FIELDS)
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_anomalies(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List anomalies for one Apstra blueprint with compact health fields."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/anomalies"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["anomalies"] = _compact_collection(out.pop("data"), _ANOMALY_FIELDS)
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_racks(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List racks in one Apstra blueprint with compact topology fields."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/racks"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["racks"] = _compact_collection(out.pop("data"), _RACK_FIELDS, ("racks",))
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_routing_zones(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List routing/security zones in one Apstra blueprint with compact fields."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/security-zones"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["routing_zones"] = _compact_collection(
            out.pop("data"),
            _ROUTING_ZONE_FIELDS,
            ("security_zones", "securityZones", "routing_zones"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_virtual_networks(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List virtual networks in one Apstra blueprint with compact bindings."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/virtual-networks"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["virtual_networks"] = _compact_collection(
            out.pop("data"),
            _VIRTUAL_NETWORK_FIELDS,
            ("virtual_networks", "virtualNetworks"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_remote_gateways(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List remote EVPN gateways in one Apstra blueprint with compact fields."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/remote_gateways"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["remote_gateways"] = _compact_collection(
            out.pop("data"),
            _REMOTE_GATEWAY_FIELDS,
            ("remote_gateways", "remoteGateways"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_connectivity_templates(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List connectivity templates visible in one Apstra blueprint."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/obj-policy-export"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["connectivity_templates"] = _compact_collection(
            out.pop("data"),
            _CONNECTIVITY_TEMPLATE_FIELDS,
            ("policies", "templates", "connectivity_templates", "obj_policies"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_application_endpoints(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List interfaces that can receive connectivity-template assignments."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/obj-policy-application-points"
    out = await _apstra_read_post(path, limit=limit, offset=offset)
    if "data" in out:
        out["application_endpoints"] = _compact_collection(
            out.pop("data"),
            _APPLICATION_ENDPOINT_FIELDS,
            ("application_points", "applicationPoints", "endpoints", "interfaces"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_get_diff_status(blueprint_id: str) -> dict[str, Any]:
    """Get compact staging-vs-active diff status for one Apstra blueprint."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/diff-status"
    out = await apstra_get(path)
    if "data" in out:
        out["diff_status"] = _compact_record(out.pop("data"), _DIFF_STATUS_FIELDS)
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_list_protocol_sessions(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List protocol sessions in one Apstra blueprint with compact status fields."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/protocol-sessions"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["protocol_sessions"] = _compact_collection(
            out.pop("data"),
            _PROTOCOL_SESSION_FIELDS,
            ("protocol_sessions", "protocolSessions", "sessions"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def apstra_get_system_info(
    blueprint_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get compact system/device information for one Apstra blueprint."""
    path = f"/api/blueprints/{_path_segment(blueprint_id)}/experience/web/system-info"
    out = await apstra_get(path, limit=limit, offset=offset)
    if "data" in out:
        out["systems"] = _compact_collection(
            out.pop("data"),
            _SYSTEM_FIELDS,
            ("systems", "nodes"),
        )
        out["blueprint_id"] = blueprint_id
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def apstra_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to Apstra with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/api/*` paths on the
    configured Apstra host. Defaults to `dry_run=True`; execution requires
    `dry_run=False` and `confirm=True`.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("apstra_write")

    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token = _apstra_config()
    if not base_url or not token:
        return {"error": "Apstra not configured. Set APSTRA_BASE_URL and APSTRA_API_TOKEN."}
    try:
        safe_path = safe_api_path(path, ("/api/",))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="Apstra")
    except ValueError as exc:
        return {"error": str(exc)}

    url = f"{base_url}{safe_path}"
    preview = {
        "method": method,
        "path": safe_path,
        "url": url,
        "params": redact_sensitive(params or {}),
        "json": redact_sensitive(body),
    }
    if dry_run:
        return {
            "dry_run": True,
            **preview,
            "execute_hint": _EXECUTE_HINT,
        }
    if not confirm:
        return {
            "error": "confirm=True is required when dry_run=False.",
            "dry_run": True,
            **preview,
        }

    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params or {},
                json=body,
            )
        return {
            "status_code": resp.status_code,
            "data": redact_sensitive(response_payload(resp)),
            "url": url,
        }
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    from mcp_servers.shared import run_server

    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    run_server(mcp)
