"""Apstra derived operation set for the generated-tools manifest.

No distributable full Apstra OpenAPI document exists. This module records
method/path mappings verified against the pinned official Juniper
``aos-sdk-api`` package, then runs them through the shared manifest builder.
Auth endpoints are provenance-only and never become model-visible tools.
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
    "name": "policy_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "Connectivity template ID.",
}
_TASK = {
    "name": "task_id",
    "in": "path",
    "required": True,
    "schema": {"type": "string"},
    "description": "Apstra blueprint task ID.",
}


def _op(operation_id: str, summary: str, tags: list[str] | None = None) -> dict:
    return {"operationId": operation_id, "summary": summary, "tags": tags or ["Apstra"]}


def _json_body() -> dict:
    return {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}


def apstra_spec() -> dict:
    paths = {
        "/api/blueprints": {
            "get": _op("listBlueprints", "List Apstra blueprints (ID/name/status)."),
            "post": {
                **_op("createBlueprint", "Create an Apstra blueprint.", ["Blueprints"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}": {
            "parameters": [_BP],
            "get": _op("getBlueprint", "Get one Apstra blueprint.", ["Blueprints"]),
            "patch": {
                **_op("updateBlueprint", "Update one Apstra blueprint.", ["Blueprints"]),
                "requestBody": _json_body(),
            },
            "delete": _op("deleteBlueprint", "Delete one Apstra blueprint.", ["Blueprints"]),
        },
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
        "/api/blueprints/{blueprint_id}/deploy": {
            "parameters": [_BP],
            "get": _op("getBlueprintDeployStatus", "Get blueprint deployment status.", ["Blueprints"]),
            "put": {
                **_op("deployBlueprint", "Deploy a blueprint.", ["Blueprints"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/configuration": {
            "parameters": [_BP],
            "get": _op("getBlueprintConfigurationStatus", "Get blueprint configuration deployment status."),
        },
        "/api/blueprints/{blueprint_id}/preview-config-summary": {
            "parameters": [_BP],
            "get": _op("previewBlueprintConfiguration", "Preview and summarize generated device configurations."),
        },
        "/api/blueprints/{blueprint_id}/diff": {
            "parameters": [_BP],
            "get": _op("getBlueprintDiff", "Get the staged-versus-deployed blueprint diff."),
        },
        "/api/blueprints/{blueprint_id}/diff-status": {
            "parameters": [_BP],
            "get": _op("getBlueprintDiffStatus", "Get staged-vs-committed diff status for one blueprint."),
        },
        "/api/blueprints/{blueprint_id}/lock-status": {
            "parameters": [_BP],
            "get": _op("getBlueprintLockStatus", "Get blueprint lock status."),
        },
        "/api/blueprints/{blueprint_id}/lock-blueprint": {
            "parameters": [_BP],
            "put": _op("lockBlueprint", "Lock a blueprint.", ["Blueprints"]),
        },
        "/api/blueprints/{blueprint_id}/unlock-blueprint": {
            "parameters": [_BP],
            "put": _op("unlockBlueprint", "Unlock a blueprint.", ["Blueprints"]),
        },
        "/api/blueprints/{blueprint_id}/revert": {
            "parameters": [_BP],
            "post": _op("revertBlueprint", "Revert a blueprint to its latest backup.", ["Blueprints"]),
        },
        "/api/blueprints/{blueprint_id}/rollback": {
            "parameters": [_BP],
            "post": {
                **_op("rollbackBlueprint", "Rollback a blueprint to a selected revision.", ["Blueprints"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/revisions": {
            "parameters": [_BP],
            "get": _op("listBlueprintRevisions", "List blueprint revisions.", ["Blueprints"]),
        },
        "/api/blueprints/{blueprint_id}/tasks": {
            "parameters": [_BP],
            "get": _op("listBlueprintTasks", "List asynchronous blueprint tasks.", ["Tasks"]),
        },
        "/api/blueprints/{blueprint_id}/tasks/{task_id}": {
            "parameters": [_BP, _TASK],
            "get": _op("getBlueprintTask", "Get asynchronous blueprint task details.", ["Tasks"]),
        },
        "/api/blueprints/{blueprint_id}/acknowledge-tasks": {
            "parameters": [_BP],
            "post": {
                **_op("acknowledgeBlueprintTasks", "Acknowledge blueprint tasks.", ["Tasks"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/policy-types": {
            "parameters": [_BP],
            "get": _op("listConnectivityTemplateTypes", "List connectivity-template types.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/endpoint-policies": {
            "parameters": [_BP],
            "get": _op("listConnectivityTemplates", "List connectivity templates in one blueprint.", ["Connectivity"]),
            "post": {
                **_op("createConnectivityTemplate", "Create a connectivity template.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/endpoint-policies/{policy_id}": {
            "parameters": [_BP, _CT],
            "get": _op("getConnectivityTemplate", "Get one connectivity template by ID.", ["Connectivity"]),
            "patch": {
                **_op("updateConnectivityTemplate", "Update a connectivity template.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
            "delete": _op("deleteConnectivityTemplate", "Delete one connectivity template by ID.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/endpoint-policies/{policy_id}/application-points": {
            "parameters": [_BP, _CT],
            "get": _op(
                "getConnectivityTemplateApplicationPoints",
                "Get valid application points for one connectivity template.",
                ["Connectivity"],
            ),
            "patch": {
                **_op(
                    "setConnectivityTemplateApplicationPoints",
                    "Update one connectivity template's application points.",
                    ["Connectivity"],
                ),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/obj-policy-export": {
            "parameters": [_BP],
            "get": _op(
                "exportObjPolicy",
                "Export all connectivity-template definitions.",
                ["Connectivity"],
            ),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-export/{policy_id}": {
            "parameters": [_BP, _CT],
            "get": _op("exportConnectivityTemplate", "Export one connectivity-template definition.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-import": {
            "parameters": [_BP],
            "put": {
                **_op("importConnectivityTemplates", "Import connectivity-template definitions.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/obj-policy-application-points": {
            "parameters": [_BP],
            "post": _op(
                "listApplicationEndpoints",
                "List application endpoints (policy application points) - read-only query POST.",
                ["Connectivity"],
            ),
        },
        "/api/blueprints/{blueprint_id}/obj-policy-batch-apply": {
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
        "/api/blueprints/{blueprint_id}/obj-policy-batch-delete": {
            "parameters": [_BP],
            "post": {
                **_op("deleteConnectivityTemplates", "Delete a batch of top-level connectivity templates.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/obj-policy-search": {
            "parameters": [_BP],
            "post": {
                **_op("searchConnectivityTemplates", "Search connectivity templates.", ["Connectivity"]),
                "requestBody": _json_body(),
            },
        },
        "/api/blueprints/{blueprint_id}/obj-policy-locations-schema": {
            "parameters": [_BP],
            "get": _op("getConnectivityLocationsSchema", "Get application-point location node types.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/experience/web/endpoint-policies": {
            "parameters": [_BP],
            "get": _op("getConnectivityTemplateStatus", "Get UI-oriented connectivity-template status.", ["Connectivity"]),
        },
        "/api/blueprints/{blueprint_id}/experience/web/obj-policies-by-application-points": {
            "parameters": [_BP],
            "post": {
                **_op(
                    "listConnectivityTemplatesByApplicationPoints",
                    "List connectivity templates for supplied application points.",
                    ["Connectivity"],
                ),
                "requestBody": _json_body(),
            },
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
        "/api/aaa/login": {
            "post": {
                **_op("apstraLogin", "Current session login (returns AuthToken).", ["Auth"]),
                "requestBody": _json_body(),
            }
        },
        "/api/user/login": {
            "post": {
                **_op("apstraLoginLegacy", "Older-release session login (returns AuthToken).", ["Auth"]),
                "requestBody": _json_body(),
            }
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HPE Juniper Apstra (derived operation set)",
            "version": "aos-sdk-api-6.1.2.post1",
            "license": {"name": "Apache-2.0 OR MIT (official Juniper SDK source mapping)"},
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
            "Pinned official Juniper aos-sdk-api 6.1.2.post1 endpoint mappings."
        ),
        "note": (
            "No distributable full Apstra OpenAPI spec is available; this reviewed SDK-derived "
            "operation set is reproducible but is not full API coverage."
        ),
        "source_url": "https://pypi.org/project/aos-sdk-api/6.1.2.post1/",
        "source_sha256": "f7774cda687655ebb7196314be8383b22a0a02890a567567b7aea0b5b3b274e3",
        "reviewed_operation_count": len(man["operations"]),
        "auth_endpoints_not_registered": ["POST /api/aaa/login", "POST /api/user/login"],
        "auth_model": "AuthToken header session (see mcp_servers/apstra.py _get_apstra_token).",
    }
    return man


if __name__ == "__main__":
    M.write_manifest("apstra", build_apstra_manifest())
    print("Wrote apstra manifest.")
