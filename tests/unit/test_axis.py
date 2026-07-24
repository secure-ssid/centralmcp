from __future__ import annotations

import asyncio

import mcp_servers.axis as axis
from mcp_servers.openapi_gen import manifest_operation_count
from mcp_servers.openapi_gen.manifest import load_manifest


class _Response:
    def __init__(self, payload=None, *, status_code=200, content=b"{}"):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.text = content.decode(errors="replace")
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError
        return self._payload


def _fake_http(monkeypatch, captured, response):
    class Client:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, kwargs=kwargs)
            return response

    monkeypatch.setattr(axis.httpx, "AsyncClient", Client)


def _configure(monkeypatch):
    monkeypatch.setenv("AXIS_API_TOKEN", "axis-secret")
    monkeypatch.delenv("AXIS_BASE_URL", raising=False)


def test_axis_manifest_registration_and_provenance():
    manifest = load_manifest("axis")
    assert manifest_operation_count("axis") == 25
    assert len(axis.GENERATED_AXIS_TOOLS) == 25
    assert len(axis.mcp._tool_manager._tools) == 25
    assert manifest["source"]["official_openapi"] is False
    assert manifest["source"]["sha256"] == (
        "79cc5e50d202497558a779a7ca4be272ce3b93e27ff27a9c859a4c5c6dee893b"
    )
    assert len([op for op in manifest["operations"] if op["capability"] == "read"]) == 12
    assert len([op for op in manifest["operations"] if op["capability"] != "read"]) == 13
    assert "axis_get_custom_ip_categories" not in axis.mcp._tool_manager._tools


def test_axis_read_uses_bearer_auth_and_bounded_pagination(monkeypatch):
    _configure(monkeypatch)
    captured = {}
    _fake_http(monkeypatch, captured, _Response({"items": list(range(250))}))

    out = asyncio.run(axis.axis_get_connectors(page_number=0, page_size=500))

    assert out["status_code"] == 200
    assert captured["url"] == "https://admin-api.axissecurity.com/api/v1.0/Connectors"
    assert captured["kwargs"]["params"] == {"pageNumber": 1, "pageSize": 100}
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer axis-secret"
    assert len(out["data"]["items"]) == 50
    assert out["data"]["_pagination"]["truncated"] is True
    schema = axis.mcp._tool_manager._tools["axis_get_connectors"].parameters
    assert "token" not in schema["properties"]


def test_axis_single_and_nested_paths_are_escaped(monkeypatch):
    _configure(monkeypatch)
    captured = {}
    _fake_http(monkeypatch, captured, _Response({"ok": True}))

    asyncio.run(axis.axis_get_sub_locations(location_id="site one", sub_location_id="floor 2"))

    assert captured["url"].endswith("/Locations/site%20one/SubLocations/floor%202")


def test_axis_writes_are_gated_and_previewed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("CENTRALMCP_AXIS_WRITES", raising=False)
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")

    blocked = asyncio.run(
        axis.axis_manage_connector(action_type="create", payload={"name": "branch"})
    )
    assert blocked["status"] == "blocked"

    monkeypatch.setenv("CENTRALMCP_AXIS_WRITES", "1")
    preview = asyncio.run(
        axis.axis_manage_connector(action_type="create", payload={"name": "branch"})
    )
    assert preview["dry_run"] is True
    assert preview["method"] == "POST"
    assert preview["path"] == "/Connectors"


def test_axis_confirmed_write_executes_and_returns_commit_hint(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("CENTRALMCP_AXIS_WRITES", "1")
    captured = {}
    _fake_http(monkeypatch, captured, _Response({"id": "c1"}))

    out = asyncio.run(
        axis.axis_manage_connector(
            action_type="update",
            connector_id="c1",
            payload={"name": "new"},
            dry_run=False,
            confirm=True,
        )
    )

    assert captured["method"] == "PUT"
    assert captured["url"].endswith("/Connectors/c1")
    assert captured["kwargs"]["json"] == {"name": "new"}
    assert out["next_step"] == "Call axis_commit_changes to apply these staged changes."


def test_axis_failed_write_does_not_return_commit_hint(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("CENTRALMCP_AXIS_WRITES", "1")
    captured = {}
    _fake_http(monkeypatch, captured, _Response({"message": "invalid"}, status_code=400))

    out = asyncio.run(
        axis.axis_manage_connector(
            action_type="create",
            payload={"name": "invalid"},
            dry_run=False,
            confirm=True,
        )
    )

    assert out["status_code"] == 400
    assert "error" in out
    assert "next_step" not in out


def test_axis_401_is_bounded_and_action_writes_require_confirmation(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("CENTRALMCP_AXIS_WRITES", "1")
    captured = {}
    _fake_http(monkeypatch, captured, _Response(status_code=401))

    no_confirm = asyncio.run(axis.axis_commit_changes(dry_run=False))
    assert "confirm=True" in no_confirm["error"]

    out = asyncio.run(axis.axis_commit_changes(dry_run=False, confirm=True))
    assert out["status_code"] == 401
    assert "expired" in out["error"]
    assert captured["timeout"] == 120.0
