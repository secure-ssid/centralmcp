"""Unit tests for the v2beta1 GLP reads (devices/device-groups/audit-log),
workspace-contact PATCH, and subscription bulk-add additions.

No live calls. Writes stay gated behind CENTRALMCP_GLP_V2BETA1_WRITES.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_servers import glp
from pipeline.clients.glp_client import GLPClient, _V2BETA1_WRITES_FLAG


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(_V2BETA1_WRITES_FLAG, raising=False)
    yield
    monkeypatch.delenv(_V2BETA1_WRITES_FLAG, raising=False)


@pytest.fixture
def writes_on(monkeypatch):
    monkeypatch.setenv(_V2BETA1_WRITES_FLAG, "1")


def _make_glp_client():
    glp_client = GLPClient.__new__(GLPClient)
    glp_client.workspace_id = "test-workspace"
    glp_client._device_id_cache = {}
    inner = MagicMock()
    glp_client._client = inner
    return glp_client, inner


# ---------------------------------------------------------------------------
# GLPClient — v2beta1 reads
# ---------------------------------------------------------------------------


class TestDevicesV2Beta1:
    def test_list_devices_v2beta1_hits_correct_endpoint(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.return_value = {"items": [{"id": "d1"}]}

        items = glp_client.list_devices_v2beta1(limit=10, offset=5, filter="archived eq false")

        assert items == [{"id": "d1"}]
        inner.get.assert_called_once_with(
            "/devices/v2beta1/devices",
            params={"limit": 10, "offset": 5, "filter": "archived eq false"},
        )

    def test_list_devices_v2beta1_raises_on_error(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="GLP list_devices_v2beta1 failed"):
            glp_client.list_devices_v2beta1()

    def test_get_device_v2beta1_returns_none_on_error(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.side_effect = RuntimeError("boom")

        assert glp_client.get_device_v2beta1("d1") is None


class TestDeviceGroupsV2Beta1:
    def test_list_device_groups_hits_correct_endpoint(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.return_value = {"items": [{"id": "g1"}]}

        items = glp_client.list_device_groups_v2beta1(limit=20)

        assert items == [{"id": "g1"}]
        inner.get.assert_called_once_with(
            "/devices/v2beta1/device-groups", params={"limit": 20, "offset": 0}
        )

    def test_get_device_group_returns_none_on_error(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.side_effect = RuntimeError("boom")

        assert glp_client.get_device_group_v2beta1("g1") is None


class TestAuditLogsV2Beta1:
    def test_list_audit_logs_v2beta1_hits_correct_endpoint(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.return_value = {"items": [{"id": "a1"}]}

        items = glp_client.list_audit_logs_v2beta1(category="DEVICE_MANAGEMENT")

        assert items == [{"id": "a1"}]
        inner.get.assert_called_once_with(
            "/audit-log/v2beta1/logs",
            params={"limit": 100, "offset": 0, "category": "DEVICE_MANAGEMENT"},
        )

    def test_get_audit_log_v2beta1_detail_hits_correct_endpoint(self, clean_env):
        glp_client, inner = _make_glp_client()
        inner.get.return_value = {"id": "a1", "detail": "..."}

        result = glp_client.get_audit_log_v2beta1_detail("a1")

        assert result == {"id": "a1", "detail": "..."}
        inner.get.assert_called_once_with("/audit-log/v2beta1/logs/a1/detail")


# ---------------------------------------------------------------------------
# GLPClient — workspace contact PATCH / subscription bulk-add (gated writes)
# ---------------------------------------------------------------------------


class TestWorkspaceContactWrite:
    def test_disabled_by_default(self, clean_env):
        glp_client, _ = _make_glp_client()
        with pytest.raises(NotImplementedError) as exc:
            glp_client.update_workspace_contact("ws1", {"email": "a@b.com"})
        assert _V2BETA1_WRITES_FLAG in str(exc.value)

    def test_enabled_sends_patch(self, clean_env, writes_on):
        glp_client, inner = _make_glp_client()
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = {"email": "a@b.com"}
        inner._request.return_value = resp

        result = glp_client.update_workspace_contact("ws1", {"email": "a@b.com"})

        assert result == {"email": "a@b.com"}
        inner._request.assert_called_once_with(
            "PATCH", "/workspaces/v1/workspaces/ws1/contact", json={"email": "a@b.com"}
        )

    def test_error_response_raises(self, clean_env, writes_on):
        glp_client, inner = _make_glp_client()
        resp = MagicMock()
        resp.is_success = False
        resp.status_code = 400
        resp.text = "bad request"
        inner._request.return_value = resp

        with pytest.raises(RuntimeError, match="HTTP 400"):
            glp_client.update_workspace_contact("ws1", {"email": "bad"})


class TestSubscriptionBulkAdd:
    def test_disabled_by_default(self, clean_env):
        glp_client, _ = _make_glp_client()
        with pytest.raises(NotImplementedError):
            glp_client.add_subscriptions(["KEY-1", "KEY-2"])

    def test_enabled_sends_post_with_keys(self, clean_env, writes_on):
        glp_client, inner = _make_glp_client()
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = {"accepted": ["KEY-1"]}
        inner._request.return_value = resp

        result = glp_client.add_subscriptions(["KEY-1"], dry_run=False)

        assert result == {"accepted": ["KEY-1"]}
        inner._request.assert_called_once_with(
            "POST",
            "/subscriptions/v1/subscriptions",
            json={"subscriptions": [{"key": "KEY-1"}]},
            params=None,
        )

    def test_dry_run_sets_query_param(self, clean_env, writes_on):
        glp_client, inner = _make_glp_client()
        resp = MagicMock()
        resp.is_success = True
        resp.json.return_value = {"valid": True}
        inner._request.return_value = resp

        glp_client.add_subscriptions(["KEY-1"], dry_run=True)

        call = inner._request.call_args
        assert call.kwargs["params"] == {"dryRun": "true"}


# ---------------------------------------------------------------------------
# glp.py MCP tool wrappers
# ---------------------------------------------------------------------------


class _StubGLP:
    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        raise AttributeError(name)


def test_list_glp_devices_v2_wraps_client(monkeypatch):
    stub = _StubGLP(list_devices_v2beta1=lambda limit, offset, filter: [{"id": "d1"}])
    monkeypatch.setattr(glp, "get_glp_client", lambda: stub)

    result = glp.list_glp_devices_v2()

    assert result == {"items": [{"id": "d1"}], "errors": []}


def test_list_glp_devices_v2_reports_errors(monkeypatch):
    def boom(limit, offset, filter):
        raise RuntimeError("no v2beta1 on this tenant")

    stub = _StubGLP(list_devices_v2beta1=boom)
    monkeypatch.setattr(glp, "get_glp_client", lambda: stub)

    result = glp.list_glp_devices_v2()

    assert result["items"] == []
    assert "no v2beta1 on this tenant" in result["errors"][0]


def test_update_glp_workspace_contact_blocked_when_writes_disabled(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_GLP_V2BETA1_WRITES", raising=False)

    result = glp.update_glp_workspace_contact("ws1", {"email": "a@b.com"})

    assert result["status"] == "FORBIDDEN"


def test_update_glp_workspace_contact_calls_client_when_enabled(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_GLP_V2BETA1_WRITES", "1")
    stub = _StubGLP(update_workspace_contact=lambda workspace_id, contact: {"ok": True})
    monkeypatch.setattr(glp, "get_glp_client", lambda: stub)

    result = glp.update_glp_workspace_contact("ws1", {"email": "a@b.com"})

    assert result == {"result": {"ok": True}, "errors": []}


def test_glp_add_subscriptions_blocked_when_writes_disabled(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_GLP_V2BETA1_WRITES", raising=False)

    result = glp.glp_add_subscriptions(["KEY-1"])

    assert result["status"] == "FORBIDDEN"


def test_list_glp_api_families_lists_explore_only_families():
    result = glp.list_glp_api_families()

    assert "/authorization/" in result["guarded_get_prefixes"]
    assert "RBAC/authorization" in result["explore_only_families"]
    assert "glp_add_subscriptions" in result["best_effort_typed_tools"]
