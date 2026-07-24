"""Apstra derived operation set for the generated-tools manifest.

No authoritative distributable full Apstra OpenAPI document exists, so this
module hand-authors the current maximum *reviewed* operation set from the
MIT-licensed upstream Apstra backend (``mcp_servers/apstra.py`` curated tools)
as a minimal in-memory OpenAPI document, then runs it through the shared
manifest builder. The two ``Auth``-tagged login endpoints are documented for
provenance but are filtered out at tool-registration time (session auth is
never a model-visible argument).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp_servers.openapi_gen import manifest as M  # noqa: E402

_BP = {
    "name": "blueprint_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "Apstra blueprint ID.",
}
_CT = {
    "name": "ct_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "Connectivity template ID.",
}


def _op(operation_id: str, summary: str, tags: list[str] | None = None) -> dict:
    return {"operationId": operation_id, "summary": summary, "tags": tags or ["Apstra"]}


def _json_body() -> dict:
    return {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}


def apstra_spec() -> dict:
    paths = {
        "/api/blueprints": {"get": _op("listBlueprints", "List Apstra blueprints (ID/name/status).")},
        "/api/design/templates": {"get": _op("listDesignTemplates", "List Apstra design templates.")},
        "/api/blueprints/{blueprint_id}/anomalies": {
            "parameters": [_BP],
            "get": _op("listBlueprintAnomalies", "List anomalies for one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/racks": {
            "parameters": [_BP],
            "get": _op("listBlueprintRacks", "List racks in one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/security-zones": {
            "parameters": [_BP],
            "get": _op("listRoutingZones", "List routing zones (security-zones) in one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/virtual-networks": {
            "parameters": [_BP],
            "get": _op("listVirtualNetworks", "List virtual networks in one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/remote_gateways": {
            "parameters": [_BP],
            "get": _op("listRemoteGateways", "List remote gateways in one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/connectivity-templates": {
            "parameters": [_BP],
            "get": _op("listConnectivityTemplates", "List connectivity templates in one blueprint."),
            "put": {
                **_op("createConnectivityTemplate", "Create or update a connectivity template.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/connectivity-templates/{ct_id}": {
            "parameters": [_BP, _CT],
            "get": _op("getConnectivityTemplate", "Get one connectivity template by ID.", ["Connectivity"]),
            "delete": _op("deleteConnectivityTemplate", "Delete one connectivity template by ID.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-export": {
            "parameters": [_BP],
            "get": _op(
                "exportObjPolicy",
                "Legacy connectivity-template catalog export (obj-policy-export).",
                ["Connectivity"],
            ),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-application-points": {
            "parameters": [_BP],
            "post": _op(
                "listApplicationEndpoints",
                "List application endpoints (policy application points) - read-only query POST.",
                ["Connectivity"],
            ),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-application-points/batch-apply": {
            "parameters": [_BP],
            "patch": {
                **_op(
                    "setApplicationPointAssignment",
                    "Batch-apply connectivity-template application point assignments.",
                    ["Connectivity"],
                ),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/diff-status": {
            "parameters": [_BP],
            "get": _op("getBlueprintDiffStatus", "Get staged-vs-committed diff status for one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/protocol-sessions": {
            "parameters": [_BP],
            "get": _op("listProtocolSessions", "List protocol (BGP) sessions in one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/experience/web/system-info": {
            "parameters": [_BP],
            "get": _op("getBlueprintSystemInfo", "Get managed system info for one blueprint."),
        },
        # Auth/login endpoints - documented for provenance; tagged Auth and
        # skipped at registration so the AuthToken session layer stays the sole
        # credential path.
        "/api/user/login": {
            "post": {**_op("apstraLogin", "Session login (returns AuthToken).", ["Auth"]), "requestBody": _json_body()}
        },
        "/api/aaa/login": {
            "post": {
                **_op("apstraLoginLegacy", "Legacy session login (returns AuthToken).", ["Auth"]),
                "requestBody": _json_body(),
            }
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HPE Juniper Apstra (derived operation set)",
            "version": "reviewed-2026-07",
            "license": {"name": "MIT (derived from upstream Apstra MCP backend operation metadata)"},
        },
        "servers": [{"url": "/"}],
        "paths": paths,
    }


def build_apstra_manifest() -> dict:
    spec = apstra_spec()
    sha = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    man = M.build_manifest(
        spec,
        platform="apstra",
        source_file="apstra-derived-operations.json",
        source_sha256=sha,
        overrides=M.load_overrides("apstra"),
    )
    man["provenance"] = {
        "acquired_from": (
            "Reviewed operation metadata from the MIT-licensed upstream Apstra MCP backend "
            "(mcp_servers/apstra.py curated tools)."
        ),
        "note": (
            "No authoritative distributable full Apstra OpenAPI spec is available; this is the "
            "current maximum reviewed operation set (NOT full OpenAPI coverage)."
        ),
        "reviewed_operation_count": len(man["operations"]),
        "auth_endpoints_not_registered": ["POST /api/user/login", "POST /api/aaa/login"],
        "auth_model": "AuthToken header session (see mcp_servers/apstra.py _get_apstra_token).",
    }
    return man


if __name__ == "__main__":
    M.write_manifest("apstra", build_apstra_manifest())
    print("Wrote apstra manifest.")
