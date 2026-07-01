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
_INTERFACE_STATE_FIELDS = (
    "id",
    "name",
    "ifName",
    "interface",
    "label",
    "lanOrWan",
    "mac",
    "ip",
    "ipAddress",
    "admin",
    "adminStatus",
    "oper",
    "operStatus",
    "state",
    "status",
    "speed",
    "duplex",
    "mtu",
    "vlan",
    "zone",
)
_DISK_REPORT_FIELDS = (
    "id",
    "name",
    "disk",
    "diskImage",
    "device",
    "controller",
    "model",
    "serial",
    "type",
    "version",
    "build",
    "release",
    "image",
    "active",
    "size",
    "capacity",
    "used",
    "free",
    "health",
    "state",
    "status",
)
_REACHABILITY_FIELDS = (
    "id",
    "nePk",
    "applianceId",
    "hostName",
    "hostname",
    "name",
    "site",
    "ipAddress",
    "reachable",
    "reachability",
    "connected",
    "rest",
    "ssh",
    "https",
    "webSocket",
    "websocket",
    "webProtocol",
    "userName",
    "username",
    "unsavedChanges",
    "lastReachable",
    "lastSeen",
    "timestamp",
    "status",
    "state",
    "reason",
    "error",
)
_REACHABILITY_PATHS = {
    "appliance": "/gms/rest/reachability/appliance",
    "gms": "/gms/rest/reachability/gms",
    "gms2": "/gms/rest/reachability/gms2",
}
_MAINTENANCE_MODE_FIELDS = (
    "id",
    "nePk",
    "applianceId",
    "hostName",
    "hostname",
    "name",
    "site",
    "maintenanceMode",
    "maintenance",
    "inMaintenance",
    "enabled",
    "status",
    "state",
    "reason",
    "description",
    "comment",
    "userName",
    "username",
    "startTime",
    "endTime",
    "timestamp",
)
_NETWORK_ROLE_SITE_FIELDS = (
    "id",
    "nePk",
    "applianceId",
    "hostName",
    "hostname",
    "name",
    "site",
    "siteId",
    "siteName",
    "siteLabel",
    "sitePriority",
    "networkRole",
    "role",
    "roleName",
    "region",
    "zone",
    "group",
    "groupName",
    "deployment",
    "state",
    "status",
)
_TOPOLOGY_LINK_FIELDS = (
    "id",
    "linkId",
    "overlayId",
    "overlayName",
    "source",
    "target",
    "srcNePk",
    "destNePk",
    "srcTunnelId",
    "destTunnelId",
    "state",
    "status",
    "operStatus",
    "adminStatus",
    "linkStatus",
)
_ROUTE_MAP_FIELDS = (
    "id",
    "name",
    "routeMap",
    "sequence",
    "description",
    "enabled",
    "prio",
    "match",
    "set",
    "metric",
    "localPreference",
    "asPathPrepend",
    "tag",
    "state",
    "status",
)
_ROUTE_LABEL_FIELDS = (
    "id",
    "labelId",
    "routeLabelId",
    "name",
    "label",
    "description",
    "color",
    "priority",
    "order",
    "tag",
    "active",
    "enabled",
    "inUse",
    "state",
    "status",
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


def _topology_node(ne_pks: list[Any], index: Any) -> Any:
    idx = _int_or_none(index)
    if idx is not None and 0 <= idx < len(ne_pks):
        return ne_pks[idx]
    return index


def _topology_pair_record(pair: Any, ne_pks: list[Any], status: str | None = None) -> Any:
    if isinstance(pair, dict):
        return pair
    if not isinstance(pair, (list, tuple)) or len(pair) < 2:
        return pair
    record: dict[str, Any] = {
        "srcNePk": _topology_node(ne_pks, pair[0]),
        "destNePk": _topology_node(ne_pks, pair[1]),
    }
    if status:
        record["status"] = status
    elif len(pair) > 2 and isinstance(pair[2], str):
        record["status"] = pair[2]
    return record


def _normalize_topology_links(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("links", "pairs"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None
    ne_pks = data.get("nePks")
    link_info = data.get("linkInfo")
    if not isinstance(ne_pks, list):
        return None
    if isinstance(link_info, list):
        out: list[Any] = []
        for status, pairs in enumerate(link_info):
            if not isinstance(pairs, list):
                continue
            out.extend(_topology_pair_record(pair, ne_pks, str(status)) for pair in pairs)
        return out
    if not isinstance(link_info, dict):
        return None

    out: list[Any] = []
    for status, pairs in link_info.items():
        if not isinstance(pairs, list):
            continue
        out.extend(_topology_pair_record(pair, ne_pks, str(status)) for pair in pairs)
    return out


def _normalize_route_maps(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("routeMaps", "maps", "policies"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None
    route_data = data.get("data")
    if isinstance(route_data, dict):
        out: list[Any] = []
        for name, value in route_data.items():
            if not isinstance(value, dict):
                continue
            record = dict(value)
            if "name" not in record and "routeMap" not in record:
                record["name"] = name
            out.append(record)
        return out
    if _looks_like_record(data, _ROUTE_MAP_FIELDS):
        return [data]
    return None


def _disk_report_records(value: Any, identity_key: str, identity_value: str) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    if _looks_like_record(value, _DISK_REPORT_FIELDS):
        record = dict(value)
        if not any(key in record for key in ("id", "name", identity_key)):
            record[identity_key] = identity_value
        return [record]

    records: list[Any] = []
    for key, item in value.items():
        if not isinstance(item, dict):
            continue
        record = dict(item)
        if not any(field in record for field in ("id", "name", identity_key)):
            record[identity_key] = key
        records.append(record)
    return records or None


def _normalize_disk_report(data: Any) -> dict[str, list[Any]] | None:
    if isinstance(data, list):
        return {"disks": data}
    if not isinstance(data, dict):
        return None

    normalized: dict[str, list[Any]] = {}
    for source_key, target_key, identity_key in (
        ("disks", "disks", "disk"),
        ("diskInfo", "disks", "disk"),
        ("controllers", "controllers", "controller"),
        ("controller", "controllers", "controller"),
        ("volumes", "volumes", "name"),
        ("diskImage", "disk_images", "diskImage"),
    ):
        records = _disk_report_records(data.get(source_key), identity_key, source_key)
        if records is not None:
            normalized.setdefault(target_key, []).extend(records)
    return normalized or None


def _compact_disk_report(data: Any, *, limit: int, offset: int) -> Any:
    records_by_key = _normalize_disk_report(data)
    if records_by_key is None:
        return _compact_collection(
            data,
            _DISK_REPORT_FIELDS,
            ("disks", "diskInfo", "controllers", "volumes"),
        )

    compacted = {
        key: [_compact_record(record, _DISK_REPORT_FIELDS) for record in records]
        for key, records in records_by_key.items()
    }
    primary_key = next(
        (key for key in ("disks", "controllers", "volumes", "disk_images") if key in compacted),
        None,
    )
    return bound_collection_response(
        compacted,
        limit=limit,
        offset=offset,
        list_key=primary_key,
    )


def _normalize_reachability_records(data: Any) -> list[Any] | None:
    records = _collection_records(
        data,
        ("appliances", "appliancesReachability", "reachability", "states"),
    )
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("appliances", "appliancesReachability", "reachability", "states", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_reachability_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _REACHABILITY_FIELDS):
        return [data]

    records = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if not any(field in record for field in ("id", "nePk", "applianceId")):
            record["nePk"] = key
        records.append(record)
    return records or None


def _compact_reachability(data: Any, *, limit: int, offset: int, single: bool = False) -> Any:
    records = _normalize_reachability_records(data)
    if records is None:
        return _compact_collection(
            data,
            _REACHABILITY_FIELDS,
            ("appliances", "appliancesReachability", "reachability", "states"),
        )

    compacted = [_compact_record(record, _REACHABILITY_FIELDS) for record in records]
    if single and len(compacted) == 1:
        return compacted[0]
    return bound_collection_response(
        {"appliances": compacted},
        limit=limit,
        offset=offset,
        list_key="appliances",
    )


def _normalize_maintenance_mode_records(data: Any) -> list[Any] | None:
    records = _collection_records(
        data,
        ("appliances", "maintenanceMode", "maintenance", "states"),
    )
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("appliances", "maintenanceMode", "maintenance", "states", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_maintenance_mode_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _MAINTENANCE_MODE_FIELDS):
        return [data]

    records = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if not any(field in record for field in ("id", "nePk", "applianceId")):
            record["nePk"] = key
        records.append(record)
    return records or None


def _compact_maintenance_mode(data: Any, *, limit: int, offset: int) -> Any:
    if isinstance(data, dict) and any(
        isinstance(data.get(key), list) for key in ("pauseOrchestration", "suppressAlarm")
    ):
        out = dict(data)
        for key in ("pauseOrchestration", "suppressAlarm"):
            if isinstance(out.get(key), list):
                out[key] = bound_collection_response(out[key], limit=limit, offset=offset)
        return out

    records = _normalize_maintenance_mode_records(data)
    if records is None:
        return _compact_collection(
            data,
            _MAINTENANCE_MODE_FIELDS,
            ("appliances", "maintenanceMode", "maintenance", "states"),
        )

    compacted = [_compact_record(record, _MAINTENANCE_MODE_FIELDS) for record in records]
    return bound_collection_response(
        {"appliances": compacted},
        limit=limit,
        offset=offset,
        list_key="appliances",
    )


def _normalize_network_role_site_records(data: Any) -> list[Any] | None:
    records = _collection_records(
        data,
        ("appliances", "networkRoleAndSite", "roles", "sites"),
    )
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("appliances", "networkRoleAndSite", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_network_role_site_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _NETWORK_ROLE_SITE_FIELDS):
        return [data]

    records = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if not any(field in record for field in ("id", "nePk", "applianceId")):
            record["nePk"] = key
        records.append(record)
    return records or None


def _compact_network_role_site(
    data: Any,
    *,
    ne_pk: str,
    limit: int,
    offset: int,
) -> Any:
    records = _normalize_network_role_site_records(data)
    if records is None:
        return _compact_collection(
            data,
            _NETWORK_ROLE_SITE_FIELDS,
            ("appliances", "networkRoleAndSite", "roles", "sites"),
        )

    compacted = []
    for record in records:
        if isinstance(record, dict) and not any(
            field in record for field in ("id", "nePk", "applianceId")
        ):
            record = {**record, "nePk": ne_pk}
        compacted.append(_compact_record(record, _NETWORK_ROLE_SITE_FIELDS))

    if len(compacted) == 1:
        return compacted[0]
    return bound_collection_response(
        {"appliances": compacted},
        limit=limit,
        offset=offset,
        list_key="appliances",
    )


def _normalize_route_label_records(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("routeLabels", "labels"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("routeLabels", "labels", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_route_label_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _ROUTE_LABEL_FIELDS):
        return [data]

    records = []
    for key, value in data.items():
        if isinstance(value, dict):
            record = dict(value)
            if not any(field in record for field in ("id", "labelId", "routeLabelId")):
                record["id"] = key
            records.append(record)
        elif isinstance(value, (str, int, float, bool)):
            records.append({"id": key, "name": value})
    return records or None


def _compact_route_labels(data: Any, *, limit: int, offset: int) -> Any:
    records = _normalize_route_label_records(data)
    if records is None:
        return _compact_collection(
            data,
            _ROUTE_LABEL_FIELDS,
            ("routeLabels", "labels"),
        )

    compacted = [_compact_record(record, _ROUTE_LABEL_FIELDS) for record in records]
    return bound_collection_response(
        {"route_labels": compacted},
        limit=limit,
        offset=offset,
        list_key="route_labels",
    )


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
async def edgeconnect_get_interface_state(
    ne_pk: str,
    cached: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get compact EdgeConnect appliance interface state by appliance nePk."""
    out = await _edgeconnect_get(
        "/gms/rest/interfaceState",
        {"nePk": ne_pk, "cached": cached},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        data = out.pop("data")
        records = _collection_records(data, ("interfaces", "interfaceStates", "ports"))
        if records is None:
            out["interface_state"] = _compact_collection(
                data,
                _INTERFACE_STATE_FIELDS,
                ("interfaces", "interfaceStates", "ports"),
            )
        else:
            compacted = [_compact_record(record, _INTERFACE_STATE_FIELDS) for record in records]
            out["interface_state"] = bound_collection_response(
                {"interfaces": compacted},
                limit=limit,
                offset=offset,
                list_key="interfaces",
            )
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_disk_report(
    ne_pk: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get compact EdgeConnect appliance disk and storage-controller report."""
    out = await _edgeconnect_get(
        "/gms/rest/configReportDisk",
        {"nePk": ne_pk},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["disk_report"] = _compact_disk_report(out.pop("data"), limit=limit, offset=offset)
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_appliance_reachability(
    ne_pk: str,
    source: str = "gms2",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get compact EdgeConnect reachability for one appliance from Orchestrator."""
    path = _REACHABILITY_PATHS.get(source)
    if path is None:
        allowed = ", ".join(sorted(_REACHABILITY_PATHS))
        return {"error": f"source must be one of: {allowed}"}

    out = await _edgeconnect_get(
        path,
        {"nePk": ne_pk},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["reachability"] = _compact_reachability(
            out.pop("data"),
            limit=limit,
            offset=offset,
            single=True,
        )
        out["ne_pk"] = ne_pk
        out["source"] = source
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_appliance_reachability(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List compact EdgeConnect appliance reachability from Orchestrator."""
    out = await _edgeconnect_get(
        "/gms/rest/reachability/gms2/appliancesReachability",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["reachability"] = _compact_reachability(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_alarms(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List outstanding EdgeConnect appliance alarms with compact fields."""
    out = await edgeconnect_get("/rest/json/alarm", limit=limit, offset=offset)
    if "data" in out:
        out["alarms"] = _compact_collection(out.pop("data"), _ALARM_FIELDS, ("outstanding",))
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_topology_link_info(
    overlay_id: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get sparse EdgeConnect topology link status for an overlay."""
    out = await _edgeconnect_get(
        "/gms/rest/gms/topologyConfig/linkInfo/v2",
        {"overlayId": overlay_id},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        data = out.pop("data")
        records = _normalize_topology_links(data)
        if records is None:
            out["topology_links"] = _compact_collection(
                data,
                _TOPOLOGY_LINK_FIELDS,
                ("links", "pairs"),
            )
        else:
            compacted = [_compact_record(record, _TOPOLOGY_LINK_FIELDS) for record in records]
            out["topology_links"] = bound_collection_response(
                compacted,
                limit=limit,
                offset=offset,
            )
        out["overlay_id"] = overlay_id
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_route_maps(
    ne_pk: str,
    cached: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get EdgeConnect route policy settings for an appliance."""
    params: dict[str, Any] = {"nePk": ne_pk}
    if cached is not None:
        params["cached"] = cached
    out = await _edgeconnect_get(
        "/gms/rest/routeMaps",
        params,
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        data = out.pop("data")
        records = _normalize_route_maps(data)
        if records is None:
            out["route_maps"] = _compact_collection(
                data,
                _ROUTE_MAP_FIELDS,
                ("routeMaps", "maps", "policies"),
            )
        else:
            compacted = [_compact_record(record, _ROUTE_MAP_FIELDS) for record in records]
            out["route_maps"] = bound_collection_response(
                compacted,
                limit=limit,
                offset=offset,
            )
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_route_labels(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List EdgeConnect route labels with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/routeLabels",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["route_labels"] = _compact_route_labels(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_route_labels(
    body: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create or update EdgeConnect route labels with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_route_labels")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/routeLabels",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


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


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_appliance_network_role_site(
    ne_pk: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get compact EdgeConnect appliance network role and site assignment."""
    out = await _edgeconnect_get(
        "/gms/rest/appliance/networkRoleAndSite",
        {"nePk": ne_pk},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["network_role_site"] = _compact_network_role_site(
            out.pop("data"),
            ne_pk=ne_pk,
            limit=limit,
            offset=offset,
        )
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_appliance_network_role_site(
    ne_pk: str,
    body: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update EdgeConnect appliance network role and site assignment with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_appliance_network_role_site")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/appliance/networkRoleAndSite",
        params={"nePk": ne_pk},
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_maintenance_mode(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List EdgeConnect appliances currently configured for maintenance mode."""
    out = await _edgeconnect_get(
        "/gms/rest/maintenanceMode",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["maintenance_mode"] = _compact_maintenance_mode(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_maintenance_mode(
    body: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Configure EdgeConnect appliance maintenance mode with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_maintenance_mode")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/maintenanceMode",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_save_changes(
    ne_pk: str | None = None,
    body: dict[str, Any] | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Persist pending EdgeConnect appliance configuration changes with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_save_changes")

    params = {"nePk": ne_pk} if ne_pk else None
    return await edgeconnect_write(
        "POST",
        "/gms/rest/appliance/saveChanges",
        params=params,
        body=body or {},
        dry_run=dry_run,
        confirm=confirm,
    )


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
