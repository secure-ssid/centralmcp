"""Unit tests for the shared generated-OpenAPI tool foundation."""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_servers.mist as mist
from mcp_servers.openapi_gen import manifest_operation_count
from mcp_servers.openapi_gen.classify import classify
from mcp_servers.openapi_gen.ir import SpecParser, UnresolvedRefError
from mcp_servers.openapi_gen.manifest import (
    build_manifest,
    build_merged_manifest,
    dumps,
    sha256_bytes,
)
from mcp_servers.openapi_gen.naming import DuplicateNameError, NameAllocator, base_name, snake
from mcp_servers.openapi_gen.runtime import register_generated_tools

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Demo", "version": "1.0", "license": {"name": "MIT"}},
    "components": {
        "parameters": {
            "org_id": {
                "name": "org_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            },
            "verbose": {
                "name": "verbose",
                "in": "query",
                "schema": {"type": "boolean", "default": False},
            },
        },
        "schemas": {
            "base": {"type": "object", "properties": {"a": {"type": "string"}}},
            "widget": {
                "allOf": [
                    {"$ref": "#/components/schemas/base"},
                    {"type": "object", "properties": {"b": {"type": "integer"}}},
                ]
            },
            "claim_codes": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
        },
    },
    "paths": {
        "/api/v1/orgs/{org_id}/widgets": {
            "get": {
                "operationId": "listWidgets",
                "summary": "List widgets",
                "parameters": [
                    {"$ref": "#/components/parameters/org_id"},
                    {"$ref": "#/components/parameters/verbose"},
                    {
                        "name": "mode",
                        "in": "query",
                        "schema": {"$ref": "#/components/schemas/mode"},
                    },
                    {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                    {"name": "Authorization", "in": "header", "schema": {"type": "string"}},
                ],
            },
            "post": {
                "operationId": "createWidget",
                "summary": "Create widget",
                "parameters": [{"$ref": "#/components/parameters/org_id"}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/widget"}}
                    },
                },
            },
            "delete": {
                "operationId": "deleteWidgets",
                "summary": "Delete widgets",
                "parameters": [{"$ref": "#/components/parameters/org_id"}],
            },
        },
    },
}


def _manifest():
    return build_manifest(
        SPEC,
        platform="demo",
        source_file="demo.json",
        source_sha256="deadbeef",
    )


# ---------------------------------------------------------------------------
# Parsing / IR
# ---------------------------------------------------------------------------

def test_parser_resolves_refs_params_and_bodies():
    ops = SpecParser(SPEC).operations()
    # Deterministic walk: methods in canonical order get,put,post,delete,...
    assert [o.method for o in ops] == ["GET", "POST", "DELETE"]
    get = ops[0]
    params = {p.name: p for p in get.parameters}
    assert params["org_id"].location == "path" and params["org_id"].required
    assert params["verbose"].schema_type == "boolean" and params["verbose"].default is False
    assert params["mode"].enum == ["fast", "slow"]
    # allOf request body resolves to an object
    post = ops[1]
    assert post.request_body.schema_type == "object"
    assert post.request_body.content_type == "application/json"
    assert post.request_body.required is True


def test_parser_array_body_item_type():
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/api/v1/x": {
                "post": {
                    "operationId": "claim",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/claim_codes"}
                            }
                        }
                    },
                }
            }
        },
        "components": SPEC["components"],
    }
    op = SpecParser(spec).operations()[0]
    assert op.request_body.schema_type == "array"
    assert op.request_body.item_type == "string"


def test_parser_raises_on_unresolved_ref():
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/api/v1/x": {
                "get": {
                    "operationId": "g",
                    "parameters": [{"$ref": "#/components/parameters/missing"}],
                }
            }
        },
    }
    with pytest.raises(UnresolvedRefError):
        SpecParser(spec).operations()


def test_parser_rejects_unsupported_version():
    with pytest.raises(Exception):
        SpecParser({"swagger": "2.0", "paths": {}})


# ---------------------------------------------------------------------------
# Naming / classification
# ---------------------------------------------------------------------------

def test_snake_and_base_name():
    assert snake("listOrgSites") == "list_org_sites"
    assert base_name("demo", "GET", "/x", "listOrgSites") == "demo_list_org_sites"


def test_name_allocator_fails_on_unresolved_duplicate():
    alloc = NameAllocator()
    alloc.allocate("demo", "GET", "/api/v1/x", "dup")
    with pytest.raises(DuplicateNameError):
        # Same method+path+operationId → base collides and digest collides too.
        alloc.allocate("demo", "GET", "/api/v1/x", "dup")


def test_name_allocator_disambiguates_distinct_paths():
    alloc = NameAllocator()
    a = alloc.allocate("demo", "GET", "/api/v1/a", "same")
    b = alloc.allocate("demo", "GET", "/api/v1/b", "same")
    assert a != b


def test_classification_defaults_and_override():
    assert classify("GET", "GET /x") == "read"
    assert classify("DELETE", "DELETE /x") == "destructive"
    assert classify("POST", "POST /x") == "write"
    assert classify("POST", "POST /x", {"POST /x": "read"}) == "read"


# ---------------------------------------------------------------------------
# Manifest determinism
# ---------------------------------------------------------------------------

def test_manifest_is_deterministic_and_records_source():
    m1 = _manifest()
    m2 = _manifest()
    assert dumps(m1) == dumps(m2)
    assert m1["source"]["sha256"] == "deadbeef"
    assert m1["source"]["operation_count"] == 3
    caps = {o["key"]: o["capability"] for o in m1["operations"]}
    assert caps["GET /api/v1/orgs/{org_id}/widgets"] == "read"
    assert caps["DELETE /api/v1/orgs/{org_id}/widgets"] == "destructive"


def test_sha256_bytes_stable():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")


def test_merged_manifest_is_deterministic_and_deduplicates_operations():
    second = {
        "openapi": "3.0.3",
        "info": {"title": "Second", "version": "2"},
        "paths": {
            "/api/v1/orgs/{org_id}/widgets": {
                "get": {
                    "operationId": "duplicateListWidgets",
                    "parameters": [{"$ref": "#/components/parameters/org_id"}],
                }
            },
            "/api/v1/health": {"get": {"operationId": "getHealth"}},
        },
        "components": {"parameters": SPEC["components"]["parameters"]},
    }
    docs = [
        ("b.json", "bbb", second),
        ("a.json", "aaa", SPEC),
    ]
    merged = build_merged_manifest(docs, platform="demo")
    assert merged["source"]["operation_count"] == 4
    assert merged["source"]["duplicate_operation_count"] == 1
    assert merged["duplicate_operations"][0]["kept_source"] == "a.json"
    assert dumps(merged) == dumps(build_merged_manifest(list(reversed(docs)), platform="demo"))


# ---------------------------------------------------------------------------
# FastMCP registration + schema
# ---------------------------------------------------------------------------

def _fake_read_executor(captured):
    async def _exec(method, path, query, headers):
        captured.update(method=method, path=path, query=query, headers=headers)
        return {"status_code": 200, "data": {"ok": True}}
    return _exec


def _fake_write_executor(captured):
    async def _exec(name, method, path, query, headers, body, content_type, dry_run, confirm):
        captured.update(
            name=name, method=method, path=path, query=query, headers=headers,
            body=body, content_type=content_type, dry_run=dry_run, confirm=confirm,
        )
        return {"dry_run": dry_run, "name": name}
    return _exec


def _register_demo(monkeypatch):
    server = FastMCP("demo-core")
    read_cap: dict = {}
    write_cap: dict = {}
    monkeypatch.setenv("CENTRALMCP_DEMO_GENERATED_TOOLS", "1")
    names = register_generated_tools(
        server,
        "demo",
        read_executor=_fake_read_executor(read_cap),
        write_executor=_fake_write_executor(write_cap),
        manifest=_manifest(),
    )
    return server, names, read_cap, write_cap


def test_registration_exposes_typed_params_without_auth(monkeypatch):
    server, names, _, _ = _register_demo(monkeypatch)
    assert len(names) == 3
    tools = server._tool_manager._tools
    get_tool = tools["demo_list_widgets"]
    props = (get_tool.parameters.get("properties") or {})
    # Typed named params exposed, not an opaque kwargs blob.
    assert "org_id" in props and "verbose" in props and "mode" in props
    # Non-auth header param exposed; auth header stripped.
    assert "x_trace" in props
    assert "authorization" not in props
    assert get_tool.annotations.readOnlyHint is True
    # Write tool exposes body/dry_run/confirm.
    post_tool = tools["demo_create_widget"]
    post_props = (post_tool.parameters.get("properties") or {})
    assert {"org_id", "body", "dry_run", "confirm"} <= set(post_props)
    assert post_tool.annotations.readOnlyHint is not True


def test_direct_read_dispatch(monkeypatch):
    server, _, read_cap, _ = _register_demo(monkeypatch)
    fn = server._tool_manager._tools["demo_list_widgets"].fn
    out = asyncio.run(fn(org_id="o1", verbose=False, mode="fast"))
    assert out["status_code"] == 200
    assert read_cap["path"] == "/api/v1/orgs/o1/widgets"
    # False preserved (not dropped), None omitted.
    assert read_cap["query"] == {"verbose": False, "mode": "fast"}


def test_read_path_escaping_and_traversal_rejection(monkeypatch):
    server, _, read_cap, _ = _register_demo(monkeypatch)
    fn = server._tool_manager._tools["demo_list_widgets"].fn
    asyncio.run(fn(org_id="a b"))
    assert read_cap["path"] == "/api/v1/orgs/a%20b/widgets"
    out = asyncio.run(fn(org_id="a/b"))
    assert "error" in out


def test_write_dispatch_passes_body_and_flags(monkeypatch):
    server, _, _, write_cap = _register_demo(monkeypatch)
    fn = server._tool_manager._tools["demo_create_widget"].fn
    asyncio.run(fn(org_id="o1", body={"a": "x"}, dry_run=True))
    assert write_cap["name"] == "demo_create_widget"
    assert write_cap["body"] == {"a": "x"}
    assert write_cap["dry_run"] is True
    assert write_cap["content_type"] == "application/json"


# ---------------------------------------------------------------------------
# Mist integration proof
# ---------------------------------------------------------------------------

def test_mist_manifest_committed_and_counts():
    assert manifest_operation_count("mist") == 1050
    assert len(mist.GENERATED_MIST_TOOLS) == 1050


def test_mist_generated_tools_registered_on_backend():
    tools = mist.mcp._tool_manager._tools
    # curated + generated
    assert "mist_status" in tools  # curated
    assert "mist_list_ap_channels" in tools  # generated read
    assert len(tools) >= 1076


def _fake_httpx(monkeypatch, captured, payload=None):
    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return payload if payload is not None else {"ok": True}

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def request(self, method, url, headers=None, params=None, **kw):
            captured.update(
                method=method, url=url, headers=headers or {}, params=params or {}, kw=kw
            )
            return Resp()

    monkeypatch.setattr(mist.httpx, "AsyncClient", FakeClient)


def test_mist_generated_read_dispatch_bounds_and_injects_auth(monkeypatch):
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    cap: dict = {}
    _fake_httpx(monkeypatch, cap, payload={"results": [1, 2, 3, 4]})
    fn = mist.mcp._tool_manager._tools["mist_list_ap_channels"].fn
    out = asyncio.run(fn(country_code="US"))
    assert cap["url"] == "https://api.mist.com/api/v1/const/ap_channels"
    assert cap["headers"]["Authorization"] == "Token secret"
    assert cap["params"] == {"country_code": "US"}
    # Response bounding applied.
    assert "_pagination" in out["data"]


def test_mist_generated_write_blocked_by_default(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_MIST_WRITES", raising=False)
    monkeypatch.delenv("CENTRALMCP_PRODUCT_ACCESS", raising=False)
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    fn = mist.mcp._tool_manager._tools["mist_claim_installer_devices"].fn
    out = asyncio.run(fn(org_id="o1", body=["CODE"]))
    assert out["status"] == "blocked"


def test_mist_generated_write_dry_run_redacts(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_MIST_WRITES", "1")
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    fn = mist.mcp._tool_manager._tools["mist_claim_installer_devices"].fn
    out = asyncio.run(fn(org_id="o1", body=["CODE"], dry_run=True))
    assert out["dry_run"] is True
    assert out["url"] == "https://api.mist.com/api/v1/installer/orgs/o1/devices"


def test_side_effecting_mist_get_uses_write_gate(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_MIST_WRITES", raising=False)
    monkeypatch.delenv("CENTRALMCP_PRODUCT_ACCESS", raising=False)
    tool = mist.mcp._tool_manager._tools["mist_optimize_installer_rrm"]
    assert tool.annotations.readOnlyHint is not True
    assert tool.annotations.idempotentHint is False
    out = asyncio.run(tool.fn(site_name="lab"))
    assert out["status"] == "blocked"


def test_generated_post_is_not_marked_idempotent():
    tool = mist.mcp._tool_manager._tools["mist_claim_installer_devices"]
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is False


def test_mist_generated_multipart_uses_httpx_files(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_MIST_WRITES", "1")
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")
    cap: dict = {}
    _fake_httpx(monkeypatch, cap)
    fn = mist.mcp._tool_manager._tools["mist_import_org_maps"].fn
    out = asyncio.run(
        fn(
            org_id="o1",
            body={"file": "map-data", "json": {"site_name": "lab"}},
            dry_run=False,
            confirm=True,
        )
    )
    assert out["status_code"] == 200
    files = cap["kw"]["files"]
    assert files["file"] == (None, "map-data")
    assert files["json"] == (None, '{"site_name": "lab"}', "application/json")
    assert "Content-Type" not in cap["headers"]


def test_mist_generated_binary_download_is_bounded(monkeypatch):
    monkeypatch.setenv("MIST_HOST", "https://api.mist.com")
    monkeypatch.setenv("MIST_API_TOKEN", "secret")

    class Resp:
        status_code = 200
        headers = {"content-type": "application/octet-stream"}
        content = b"x" * 200_000
        text = ""

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, headers=None, params=None, **kwargs):
            return Resp()

    monkeypatch.setattr(mist.httpx, "AsyncClient", FakeClient)
    fn = mist.mcp._tool_manager._tools["mist_download_site_rfdiag_recording"].fn
    out = asyncio.run(fn(site_id="s1", rfdiag_id="r1"))
    payload = out["data"]
    assert payload["size_bytes"] == 200_000
    assert payload["truncated"] is True
    assert len(payload["base64"]) < 200_000


# ---------------------------------------------------------------------------
# Generation CLI drift/check mode (skipped when the local spec is absent)
# ---------------------------------------------------------------------------

def test_committed_mist_manifest_matches_fresh_build():
    from pathlib import Path

    from mcp_servers.openapi_gen import manifest as manifest_mod
    from scripts import generate_openapi_tools as cli

    spec_path = Path(cli._REPO_ROOT) / cli._DEFAULT_SPECS["mist"]
    if not spec_path.exists():
        pytest.skip("local Mist spec not present (gitignored)")
    fresh = manifest_mod.dumps(cli.build("mist", spec_path))
    committed = manifest_mod.manifest_path("mist").read_text()
    assert fresh == committed, "committed Mist manifest is stale; re-run generate_openapi_tools.py"
