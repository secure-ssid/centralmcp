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
_INTERFACE_LABEL_FIELDS = (
    "id",
    "labelId",
    "name",
    "label",
    "type",
    "interfaceType",
    "topology",
    "active",
    "enabled",
    "inUse",
    "description",
    "state",
    "status",
)
_BYPASS_FIELDS = (
    "bypass_actual",
    "bypass_config",
    "bypassActual",
    "bypassConfig",
    "enabled",
    "enable",
    "supportsBypass",
    "status",
    "state",
    "message",
    "error",
)
_LINK_INTEGRITY_FIELDS = (
    "active",
    "result",
    "status",
    "state",
    "taskKey",
    "clientKey",
    "message",
    "error",
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
_IP_OBJECT_GROUP_FIELDS = (
    "id",
    "name",
    "type",
    "description",
    "rules",
    "includedIPs",
    "excludedIPs",
    "includedGroups",
    "includedServices",
    "services",
    "protocol",
    "ports",
    "tcpPorts",
    "udpPorts",
    "icmpTypes",
    "comment",
    "active",
    "enabled",
    "state",
    "status",
)
_SERVICE_FIELDS = (
    "id",
    "name",
    "img",
    "peerName",
    "enabled",
    "provider",
    "type",
    "description",
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
_ZONE_FIELDS = (
    "id",
    "zoneId",
    "zoneIndex",
    "name",
    "zoneName",
    "description",
    "vrfId",
    "vrfName",
    "segmentId",
    "segmentName",
    "active",
    "enabled",
    "enable",
    "state",
    "status",
)
_ZONE_STATUS_FIELDS = (
    "enable",
    "enabled",
    "state",
    "status",
)
_NEXT_ID_FIELDS = (
    "nextId",
    "next_id",
    "id",
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


def _trim_text(value: Any, max_chars: int) -> Any:
    if not isinstance(value, str):
        return value
    max_chars = max(0, max_chars)
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "..."


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


def _normalize_ip_object_group_records(data: Any) -> list[Any] | None:
    records = _collection_records(
        data,
        ("addressGroups", "serviceGroups", "groups", "ipObjects"),
    )
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("addressGroups", "serviceGroups", "groups", "ipObjects", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_ip_object_group_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _IP_OBJECT_GROUP_FIELDS):
        return [data]

    records = []
    for name, value in data.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if "name" not in record:
            record["name"] = name
        records.append(record)
    return records or None


def _compact_ip_object_groups(
    data: Any,
    *,
    limit: int,
    offset: int,
    list_key: str,
) -> Any:
    records = _normalize_ip_object_group_records(data)
    if records is None:
        return _compact_collection(
            data,
            _IP_OBJECT_GROUP_FIELDS,
            ("addressGroups", "serviceGroups", "groups", "ipObjects"),
        )

    compacted = [_compact_record(record, _IP_OBJECT_GROUP_FIELDS) for record in records]
    return bound_collection_response(
        {list_key: compacted},
        limit=limit,
        offset=offset,
        list_key=list_key,
    )


def _normalize_service_records(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("services", "thirdPartyServices"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("services", "thirdPartyServices", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_service_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _SERVICE_FIELDS):
        return [data]

    records = []
    for service_id, value in data.items():
        if not isinstance(value, dict):
            continue
        record = dict(value)
        if "id" not in record:
            record["id"] = service_id
        records.append(record)
    return records or None


def _compact_services(data: Any, *, limit: int, offset: int, list_key: str) -> Any:
    records = _normalize_service_records(data)
    if records is None:
        return _compact_collection(
            data,
            _SERVICE_FIELDS,
            ("services", "thirdPartyServices"),
        )

    compacted = [_compact_record(record, _SERVICE_FIELDS) for record in records]
    return bound_collection_response(
        {list_key: compacted},
        limit=limit,
        offset=offset,
        list_key=list_key,
    )


def _interface_label_record(
    label_id: Any,
    label: Any,
    label_type: str | None = None,
) -> Any:
    if isinstance(label, dict):
        record = dict(label)
        if not any(field in record for field in ("id", "labelId")):
            record["id"] = label_id
        if label_type and not any(field in record for field in ("type", "interfaceType")):
            record["type"] = label_type
        return record
    if isinstance(label, (str, int, float, bool)):
        record = {"id": label_id, "name": label}
        if label_type:
            record["type"] = label_type
        return record
    return label


def _normalize_interface_label_records(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("interfaceLabels", "labels"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("interfaceLabels", "labels", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_interface_label_records(nested)
            if records is not None:
                return records

    records = []
    for label_type in ("wan", "lan"):
        labels = data.get(label_type)
        if isinstance(labels, dict):
            for label_id, label in labels.items():
                record = _interface_label_record(label_id, label, label_type)
                if isinstance(record, dict):
                    records.append(record)
        elif isinstance(labels, list):
            for label in labels:
                record = _interface_label_record(None, label, label_type)
                if isinstance(record, dict):
                    records.append(record)
    if records:
        return records

    if _looks_like_record(data, _INTERFACE_LABEL_FIELDS):
        return [data]

    for key, value in data.items():
        if _int_or_none(key) is None and not (
            isinstance(value, dict) and _looks_like_record(value, _INTERFACE_LABEL_FIELDS)
        ):
            continue
        record = _interface_label_record(key, value)
        if isinstance(record, dict):
            records.append(record)
    return records or None


def _compact_interface_labels(data: Any, *, limit: int, offset: int) -> Any:
    records = _normalize_interface_label_records(data)
    if records is None:
        return _compact_collection(
            data,
            _INTERFACE_LABEL_FIELDS,
            ("interfaceLabels", "labels"),
        )

    compacted = [_compact_record(record, _INTERFACE_LABEL_FIELDS) for record in records]
    return bound_collection_response(
        {"interface_labels": compacted},
        limit=limit,
        offset=offset,
        list_key="interface_labels",
    )


def _normalize_zone_records(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("zones", "zoneList", "zoneNames"))
    if records is not None:
        return records
    if not isinstance(data, dict):
        return None

    for key in ("zones", "data"):
        nested = data.get(key)
        if isinstance(nested, dict):
            records = _normalize_zone_records(nested)
            if records is not None:
                return records

    if _looks_like_record(data, _ZONE_FIELDS):
        return [data]

    records = []
    for key, value in data.items():
        zone_id = _int_or_none(key)
        if isinstance(value, dict):
            if zone_id is None and not _looks_like_record(value, _ZONE_FIELDS):
                continue
            record = dict(value)
            if zone_id is not None and not any(field in record for field in ("id", "zoneId")):
                record["id"] = zone_id
            records.append(record)
        elif zone_id is not None and isinstance(value, (str, int, float, bool)):
            records.append({"id": zone_id, "name": value})
    return records or None


def _compact_zones(data: Any, *, limit: int, offset: int) -> Any:
    records = _normalize_zone_records(data)
    if records is None:
        return _compact_collection(
            data,
            _ZONE_FIELDS,
            ("zones", "zoneList", "zoneNames"),
        )

    compacted = [_compact_record(record, _ZONE_FIELDS) for record in records]
    return bound_collection_response(
        {"zones": compacted},
        limit=limit,
        offset=offset,
        list_key="zones",
    )


def _compact_zone_status(data: Any) -> Any:
    if isinstance(data, bool):
        return {"enable": data}
    compacted = _compact_record(data, _ZONE_STATUS_FIELDS)
    return compacted or data


def _compact_next_id(data: Any) -> Any:
    if isinstance(data, int):
        return {"nextId": data}
    compacted = _compact_record(data, _NEXT_ID_FIELDS)
    return compacted or data


def _compact_link_integrity_status(data: Any, *, max_result_chars: int) -> Any:
    compacted = _compact_record(data, _LINK_INTEGRITY_FIELDS)
    if not isinstance(compacted, dict):
        return data
    if "result" in compacted:
        original = compacted["result"]
        trimmed = _trim_text(original, max_result_chars)
        compacted["result"] = trimmed
        compacted["result_truncated"] = isinstance(original, str) and trimmed != original
    return compacted


def _normalize_vrf_zone_map_records(data: Any) -> list[Any] | None:
    records = _collection_records(data, ("zones", "items"))
    if records is not None:
        return records
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None

    records = []
    for vrf_key, zones in data.items():
        if not isinstance(zones, dict):
            continue
        vrf_id = _int_or_none(vrf_key)
        for zone_index_key, zone in zones.items():
            if not isinstance(zone, dict):
                continue
            if _int_or_none(zone.get("id")) == 255:
                continue
            record = dict(zone)
            zone_index = _int_or_none(zone_index_key)
            if vrf_id is not None and "vrfId" not in record:
                record["vrfId"] = vrf_id
            if zone_index is not None and "zoneIndex" not in record:
                record["zoneIndex"] = zone_index
            records.append(record)
    return records or None


def _compact_vrf_zones(data: Any, *, limit: int, offset: int) -> Any:
    records = _normalize_vrf_zone_map_records(data)
    if records is None:
        return _compact_collection(
            data,
            _ZONE_FIELDS,
            ("zones", "items"),
        )

    compacted = [_compact_record(record, _ZONE_FIELDS) for record in records]
    return bound_collection_response(
        {"zones": compacted},
        limit=limit,
        offset=offset,
        list_key="zones",
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
async def edgeconnect_list_interface_labels(
    label_type: str | None = None,
    active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect interface labels with compact WAN/LAN fields."""
    params: dict[str, Any] = {}
    if label_type is not None:
        label_type = label_type.strip().lower()
        if label_type not in {"wan", "lan"}:
            return {"error": "label_type must be one of: lan, wan"}
        params["type"] = label_type
    if active is not None:
        params["active"] = active

    out = await _edgeconnect_get(
        "/gms/rest/gms/interfaceLabels",
        params or None,
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["interface_labels"] = _compact_interface_labels(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
        if label_type is not None:
            out["label_type"] = label_type
        if active is not None:
            out["active"] = active
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_interface_labels(
    body: dict[str, Any],
    delete_dependencies: bool | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Replace EdgeConnect interface labels with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_interface_labels")

    params = (
        {"deleteDependencies": delete_dependencies}
        if delete_dependencies is not None
        else None
    )
    return await edgeconnect_write(
        "POST",
        "/gms/rest/gms/interfaceLabels",
        params=params,
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_apply_interface_labels(
    ne_pk: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Push active EdgeConnect interface labels to one appliance with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_apply_interface_labels")
    if not ne_pk.strip():
        return {"error": "ne_pk is required."}

    return await edgeconnect_write(
        "POST",
        "/gms/rest/interfaceLabels",
        params={"nePk": ne_pk.strip()},
        body={},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_bypass_mode(
    ne_pk: str,
    cached: bool = True,
) -> dict[str, Any]:
    """Get compact EdgeConnect bypass-mode state for an appliance."""
    out = await _edgeconnect_get(
        "/gms/rest/bypass",
        {"nePk": ne_pk, "cached": str(cached).lower()},
        limit=1,
        offset=0,
        paginate=False,
    )
    if "data" in out:
        data = out.pop("data")
        out["bypass_mode"] = _compact_record(data, _BYPASS_FIELDS) or data
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_bypass_mode(
    enabled: bool,
    ne_pks: list[str],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Enable or disable EdgeConnect bypass mode on appliances with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_bypass_mode")
    cleaned_ne_pks = [ne_pk.strip() for ne_pk in ne_pks if ne_pk.strip()]
    if not cleaned_ne_pks:
        return {"error": "ne_pks must include at least one appliance nePk."}

    return await edgeconnect_write(
        "POST",
        "/gms/rest/bypass",
        body={"enable": enabled, "nePks": cleaned_ne_pks},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_link_integrity_status(
    ne_pk: str,
    max_result_chars: int = 1200,
) -> dict[str, Any]:
    """Get compact EdgeConnect link-integrity test status for an appliance."""
    out = await _edgeconnect_get(
        "/gms/rest/linkIntegrityTest/status",
        {"nePk": ne_pk},
        limit=1,
        offset=0,
        paginate=False,
    )
    if "data" in out:
        out["link_integrity_status"] = _compact_link_integrity_status(
            out.pop("data"),
            max_result_chars=max_result_chars,
        )
        out["ne_pk"] = ne_pk
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_run_link_integrity_test(
    body: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Start an EdgeConnect link-integrity iperf/tcpperf test with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_run_link_integrity_test")

    ne_pks = body.get("nePks")
    if not isinstance(ne_pks, list) or len([item for item in ne_pks if str(item).strip()]) != 2:
        return {"error": "body.nePks must contain exactly two appliance nePk values."}

    return await edgeconnect_write(
        "POST",
        "/gms/rest/linkIntegrityTest/run",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


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
async def edgeconnect_list_address_groups(
    name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect ACL address groups with compact fields."""
    params = {"name": name.strip()} if name and name.strip() else None
    out = await _edgeconnect_get(
        "/gms/rest/ipObjects/addressGroup",
        params,
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["address_groups"] = _compact_ip_object_groups(
            out.pop("data"),
            limit=limit,
            offset=offset,
            list_key="address_groups",
        )
        if params:
            out["name"] = params["name"]
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_address_group(
    body: dict[str, Any],
    replace_existing: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create/update or replace an EdgeConnect ACL address group with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_address_group")

    return await edgeconnect_write(
        "PUT" if replace_existing else "POST",
        "/gms/rest/ipObjects/addressGroup",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_delete_address_group(
    name: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete an EdgeConnect ACL address group by name with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_delete_address_group")
    name = name.strip()
    if not name:
        return {"error": "name is required."}

    return await edgeconnect_write(
        "DELETE",
        "/gms/rest/ipObjects/addressGroup",
        params={"name": name},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_service_groups(
    name: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect ACL service groups with compact fields."""
    params = {"name": name.strip()} if name and name.strip() else None
    out = await _edgeconnect_get(
        "/gms/rest/ipObjects/serviceGroup",
        params,
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["service_groups"] = _compact_ip_object_groups(
            out.pop("data"),
            limit=limit,
            offset=offset,
            list_key="service_groups",
        )
        if params:
            out["name"] = params["name"]
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_service_group(
    body: dict[str, Any],
    replace_existing: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create/update or replace an EdgeConnect ACL service group with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_service_group")

    return await edgeconnect_write(
        "PUT" if replace_existing else "POST",
        "/gms/rest/ipObjects/serviceGroup",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_delete_service_group(
    name: str,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete an EdgeConnect ACL service group by name with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_delete_service_group")
    name = name.strip()
    if not name:
        return {"error": "name is required."}

    return await edgeconnect_write(
        "DELETE",
        "/gms/rest/ipObjects/serviceGroup",
        params={"name": name},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_services(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List EdgeConnect overlay internet services with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/gms/services",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["services"] = _compact_services(
            out.pop("data"),
            limit=limit,
            offset=offset,
            list_key="services",
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_third_party_services(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect third-party cloud services with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/gms/thirdPartyServices",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["third_party_services"] = _compact_services(
            out.pop("data"),
            limit=limit,
            offset=offset,
            list_key="third_party_services",
        )
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_services(
    body: dict[str, Any],
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Replace EdgeConnect overlay internet services with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_services")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/gms/services",
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_zones(
    all_vrf_zones: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect firewall zones with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/zones",
        {"allVRFZones": all_vrf_zones},
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["zones"] = _compact_zones(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
        out["all_vrf_zones"] = all_vrf_zones
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_zone_firewall_status() -> dict[str, Any]:
    """Get EdgeConnect End-to-End Zone-Based Firewall status."""
    out = await _edgeconnect_get(
        "/gms/rest/zones/eeEnable",
        limit=1,
        offset=0,
        paginate=False,
    )
    if "data" in out:
        out["zone_firewall_status"] = _compact_zone_status(out.pop("data"))
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_get_next_zone_id() -> dict[str, Any]:
    """Get the next available EdgeConnect firewall-zone ID."""
    out = await _edgeconnect_get(
        "/gms/rest/zones/nextId",
        limit=1,
        offset=0,
        paginate=False,
    )
    if "data" in out:
        out["next_zone_id"] = _compact_next_id(out.pop("data"))
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_vrf_segment_zones(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect firewall zones across VRF segments with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/zones/vrfSegmentZonesMap",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["vrf_segment_zones"] = _compact_vrf_zones(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=READ_ONLY)
async def edgeconnect_list_vrf_zone_map(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List EdgeConnect VRF-to-firewall-zone mappings with compact fields."""
    out = await _edgeconnect_get(
        "/gms/rest/zones/vrfZonesMap",
        limit=limit,
        offset=offset,
        paginate=False,
    )
    if "data" in out:
        out["vrf_zone_map"] = _compact_vrf_zones(
            out.pop("data"),
            limit=limit,
            offset=offset,
        )
    return out


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_zones(
    body: dict[str, Any],
    delete_dependencies: bool = False,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Replace EdgeConnect firewall zones with write guards.

    The upstream endpoint treats `body` as the complete zone map; omitted zones
    are deleted by Orchestrator.
    """
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_zones")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/zones",
        params={"deleteDependencies": delete_dependencies},
        body=body,
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_zone_firewall_status(
    enabled: bool,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Enable or disable EdgeConnect End-to-End Zone-Based Firewall with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_zone_firewall_status")

    return await edgeconnect_write(
        "POST",
        "/gms/rest/zones/eeEnable",
        body={"enable": enabled},
        dry_run=dry_run,
        confirm=confirm,
    )


@mcp.tool(annotations=DESTRUCTIVE)
async def edgeconnect_set_next_zone_id(
    next_id: int,
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Set the next available EdgeConnect firewall-zone ID with write guards."""
    if not optional_product_writes_allowed():
        return optional_product_write_blocked("edgeconnect_set_next_zone_id")
    if next_id < 1:
        return {"error": "next_id must be greater than 0."}

    return await edgeconnect_write(
        "POST",
        "/gms/rest/zones/nextId",
        body={"nextId": next_id},
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
