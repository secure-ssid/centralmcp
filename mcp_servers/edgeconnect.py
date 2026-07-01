"""MCP server — optional HPE Aruba EdgeConnect backend starter tools.

Enabled via tool router env:
  CENTRALMCP_PRODUCTS=edgeconnect

Auth/env:
  EDGECONNECT_BASE_URL    e.g. https://orchestrator.example.com
  EDGECONNECT_API_TOKEN   API token
  EDGECONNECT_AUTH_HEADER Header name; defaults to Authorization
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

mcp = FastMCP("edgeconnect-core")
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXECUTE_HINT = "Review the request, then call again with dry_run=False and confirm=True."
_APPLIANCE_FIELDS = (
    "id",
    "nePk",
    "hostName",
    "hostname",
    "name",
    "site",
    "model",
    "serial",
    "serialNumber",
    "softwareVersion",
    "ipAddress",
    "state",
    "status",
)
_SYSTEM_INFO_FIELDS = (
    "hostName",
    "applianceid",
    "model",
    "modelShort",
    "platform",
    "status",
    "uptimeString",
    "release",
    "serial",
    "uuid",
    "deploymentMode",
    "alarmSummary",
)
_ALARM_FIELDS = (
    "id",
    "uuid",
    "type",
    "severity",
    "state",
    "source",
    "message",
    "description",
    "timestamp",
    "time",
)
_OVERLAY_FIELDS = (
    "id",
    "overlayId",
    "name",
    "overlayName",
    "displayName",
    "description",
    "mode",
    "topology",
    "type",
    "enabled",
    "state",
    "status",
)
_OVERLAY_PRIORITY_FIELDS = (
    "id",
    "overlayId",
    "name",
    "overlayName",
    "priority",
    "order",
    "rank",
)
_TUNNEL_FIELDS = (
    "id",
    "tunnelId",
    "alias",
    "tag",
    "srcNePk",
    "destNePk",
    "destTunnelId",
    "destTunnelAlias",
    "operStatus",
    "adminStatus",
    "remoteIdState",
    "fecStatus",
    "fecRatio",
    "state",
    "status",
)
_TUNNEL_METADATA_FIELDS = (
    "total",
    "count",
    "totalTunnels",
    "tunnelCount",
    "physical",
    "physicalTunnels",
    "bonded",
    "bondedTunnels",
    "thirdParty",
    "thirdPartyTunnels",
    "ipsec",
    "ipsecTunnels",
)
_VRF_SEGMENT_FIELDS = (
    "id",
    "segmentId",
    "name",
    "segmentName",
    "vrf",
    "vrfName",
    "description",
    "enabled",
    "state",
    "status",
)


def _edgeconnect_config() -> tuple[str | None, str | None, str]:
    import os

    base_url = os.getenv("EDGECONNECT_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("EDGECONNECT_API_TOKEN", "").strip()
    header = os.getenv("EDGECONNECT_AUTH_HEADER", "Authorization").strip() or "Authorization"
    return (base_url or None, token or None, header)


def _compact_record(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return item
    return {key: item[key] for key in fields if key in item}


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


def _collection_records(data: Any, list_keys: tuple[str, ...]) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for key in (*list_keys, "items", "results", "data"):
        if isinstance(data.get(key), list):
            return data[key]
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_vrf_segments(data: Any) -> list[Any] | None:
    if not isinstance(data, dict):
        return None

    records: list[Any] = []
    for key, value in data.items():
        segment_id = _int_or_none(key)
        if segment_id is None or not isinstance(value, dict):
            continue
        record = dict(value)
        if "id" not in record and "segmentId" not in record:
            record["id"] = segment_id
        records.append(record)
    return records or None


def _matches_vrf_segment(record: Any, segment_id: int | None) -> bool:
    if segment_id is None:
        return True
    if not isinstance(record, dict):
        return False
    for key in ("id", "segmentId"):
        if _int_or_none(record.get(key)) == segment_id:
            return True
    return False


def _normalize_id_keyed_records(data: Any, id_key: str) -> list[Any] | None:
    if not isinstance(data, dict):
        return None

    records: list[Any] = []
    for key, value in data.items():
        record_id = _int_or_none(key)
        if record_id is None or not isinstance(value, dict):
            continue
        record = dict(value)
        if id_key not in record and "id" not in record:
            record[id_key] = record_id
        records.append(record)
    return records or None


def _matches_id(record: Any, target_id: int | None, keys: tuple[str, ...]) -> bool:
    if target_id is None:
        return True
    if not isinstance(record, dict):
        return False
    return any(_int_or_none(record.get(key)) == target_id for key in keys)


def _looks_like_record(data: Any, fields: tuple[str, ...]) -> bool:
    return isinstance(data, dict) and any(key in data for key in fields)


@mcp.tool(annotations=READ_ONLY)
def edgeconnect_status() -> dict[str, Any]:
    """Report whether EdgeConnect backend is configured."""
    base_url, token, header = _edgeconnect_config()
    return {
        "configured": bool(base_url and token),
        "base_url": base_url,
        "has_token": bool(token),
        "auth_header": header,
    }


async def _edgeconnect_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
    paginate: bool = True,
) -> dict[str, Any]:
    base_url, token, header = _edgeconnect_config()
    if not base_url or not token:
        return {
            "error": "EdgeConnect not configured. Set EDGECONNECT_BASE_URL and "
            "EDGECONNECT_API_TOKEN."
        }
    try:
        path = safe_api_path(path, ("/gms/rest/", "/rest/json/"))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    path = quote(path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="EdgeConnect")
    except ValueError as exc:
        return {"error": str(exc)}
    url = f"{base_url}{path}"
    auth_value = "Bearer " + token if header.lower() == "authorization" else token
    headers = {header: auth_value, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params or {})
        payload = response_payload(resp)
        if paginate:
            payload = bound_collection_response(payload, limit=limit, offset=offset)
        return {"status_code": resp.status_code, "data": payload, "url": url}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "url": url}


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get(
    path: str,
    params: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Perform a read-only GET request to EdgeConnect Orchestrator API.

    Safety guard: only allows paths beginning with `/gms/rest/` or `/rest/json/`.
    List payloads are bounded with `limit` and `offset`.
    """
    out = await _edgeconnect_get(path, params, limit=limit, offset=offset, paginate=False)
    if "data" in out:
        out["data"] = bound_collection_response(out["data"], limit=limit, offset=offset)
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_appliances(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List EdgeConnect Orchestrator appliances with compact inventory fields."""
    out = await edgeconnect_get("/gms/rest/appliance", limit=limit, offset=offset)
    if "data" in out:
        out["appliances"] = _compact_collection(out.pop("data"), _APPLIANCE_FIELDS)
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_system_info() -> dict[str, Any]:
    """Get compact system information from an EdgeConnect appliance API."""
    out = await edgeconnect_get("/rest/json/systemInfo")
    if "data" in out:
        out["system_info"] = _compact_record(out.pop("data"), _SYSTEM_INFO_FIELDS)
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_alarms(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List outstanding EdgeConnect appliance alarms with compact fields."""
    out = await edgeconnect_get("/rest/json/alarm", limit=limit, offset=offset)
    if "data" in out:
        out["alarms"] = _compact_collection(out.pop("data"), _ALARM_FIELDS, ("outstanding",))
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_overlays(
    overlay_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect overlay configurations with compact fields."""
    params: dict[str, Any] = {}
    if overlay_id is not None:
        params["overlayId"] = overlay_id
    out = await _edgeconnect_get(
        "/gms/rest/gms/overlays/config",
        params,
        limit=limit,
        offset=offset,
        paginate=overlay_id is None,
    )
    if "data" in out:
        data = out.pop("data")
        records = _normalize_id_keyed_records(data, "overlayId")
        if records is None and overlay_id is not None:
            records = _collection_records(data, ("overlays",))

        if records is not None:
            filtered = [
                record for record in records if _matches_id(record, overlay_id, ("id", "overlayId"))
            ]
            compacted = [_compact_record(record, _OVERLAY_FIELDS) for record in filtered]
            out["overlays"] = bound_collection_response(compacted, limit=limit, offset=offset)
        elif overlay_id is not None and _looks_like_record(data, _OVERLAY_FIELDS):
            compacted = _compact_record(data, _OVERLAY_FIELDS)
            out["overlays"] = bound_collection_response([compacted], limit=limit, offset=offset)
        else:
            out["overlays"] = _compact_collection(data, _OVERLAY_FIELDS, ("overlays",))
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_overlay_priority(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get EdgeConnect overlay priority order mapping."""
    out = await edgeconnect_get(
        "/gms/rest/gms/overlays/priority",
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        data = out.pop("data")
        records = _normalize_id_keyed_records(data, "overlayId")
        if records is None:
            out["overlay_priority"] = _compact_collection(
                data,
                _OVERLAY_PRIORITY_FIELDS,
                ("overlays", "priorities"),
            )
        else:
            compacted = [_compact_record(record, _OVERLAY_PRIORITY_FIELDS) for record in records]
            out["overlay_priority"] = bound_collection_response(
                compacted,
                limit=limit,
                offset=offset,
            )
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_tunnels(
    ne_pk: str | None = None,
    tunnel_id: str | None = None,
    state: str | None = None,
    matching_alias: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect physical tunnels with compact health/status fields."""
    params: dict[str, Any] = {"limit": limit}
    if ne_pk:
        params["nePk"] = ne_pk
    if tunnel_id:
        params["tunnelId"] = tunnel_id
    if state:
        params["state"] = state
    if matching_alias:
        params["matchingAlias"] = matching_alias

    out = await edgeconnect_get(
        "/gms/rest/tunnels2/physical",
        params,
        limit=limit,
        offset=offset,
    )
    if "data" in out:
        out["tunnels"] = _compact_collection(
            out.pop("data"),
            _TUNNEL_FIELDS,
            ("tunnels", "physicalTunnels"),
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_tunnel_metadata() -> dict[str, Any]:
    """Get EdgeConnect tunnel count metadata from Orchestrator."""
    out = await edgeconnect_get("/gms/rest/tunnels2", {"metaData": True})
    if "data" in out:
        data = out.pop("data")
        metadata = _compact_record(data, _TUNNEL_METADATA_FIELDS)
        out["tunnel_metadata"] = metadata or data
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_vrf_segments(
    segment_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect routing/VRF segments with compact fields."""
    params: dict[str, Any] = {}
    if segment_id is not None:
        params["id"] = segment_id
    out = await _edgeconnect_get(
        "/gms/rest/vrf/config/segments",
        params,
        limit=limit,
        offset=offset,
        paginate=segment_id is None,
    )
    if "data" in out:
        data = out.pop("data")
        records = _normalize_vrf_segments(data)
        if records is None and segment_id is not None:
            records = _collection_records(data, ("segments", "vrfs"))
        if records is None:
            out["vrf_segments"] = _compact_collection(
                data,
                _VRF_SEGMENT_FIELDS,
                ("segments", "vrfs"),
            )
        else:
            filtered = [
                record for record in records if _matches_vrf_segment(record, segment_id)
            ]
            compacted = [_compact_record(record, _VRF_SEGMENT_FIELDS) for record in filtered]
            out["vrf_segments"] = bound_collection_response(
                compacted,
                limit=limit,
                offset=offset,
            )
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_write(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | list[Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Perform a lab write request to EdgeConnect with a preview-first guard.

    Allows `POST`, `PUT`, `PATCH`, and `DELETE` against `/gms/rest/*` or
    `/rest/json/*` paths on the configured Orchestrator host. Defaults to
    `dry_run=True`; execution requires `dry_run=False` and `confirm=True`.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_write")

    method = method.upper()
    if method not in _WRITE_METHODS:
        return {"error": f"method must be one of: {', '.join(sorted(_WRITE_METHODS))}"}

    base_url, token, header = _edgeconnect_config()
    if not base_url or not token:
        return {
            "error": "EdgeConnect not configured. Set EDGECONNECT_BASE_URL and "
            "EDGECONNECT_API_TOKEN."
        }
    try:
        safe_path = safe_api_path(path, ("/gms/rest/", "/rest/json/"))
    except ValueError as exc:
        return {"error": f"Invalid path. {exc}"}
    safe_path = quote(safe_path, safe="/")

    try:
        base_url = validate_product_base_url(base_url, product="EdgeConnect")
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

    auth_value = "Bearer " + token if header.lower() == "authorization" else token
    headers = {header: auth_value, "Accept": "application/json"}
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
