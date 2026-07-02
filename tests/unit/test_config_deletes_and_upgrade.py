"""Regression coverage for config.py's highest-risk previously-untested
DESTRUCTIVE tools: irreversible deletes and the firmware upgrade trigger.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_servers import config

# (tool, kwargs, endpoint_suffix, method)
SIMPLE_DELETE_TOOLS = [
    (config.delete_role_acl, {"name": "acl1"}, "/role-acls/acl1", "DELETE"),
    (config.delete_gw_policy, {"name": "policy1"}, "/policies/policy1", "DELETE"),
    (
        config.delete_config_assignment,
        {"scope_id": "s1", "device_function": "ACCESS_SWITCH", "profile_type": "roles", "profile_instance": "arubaSW"},
        "/config-assignments/s1/ACCESS_SWITCH/roles/arubaSW",
        "DELETE",
    ),
    (config.delete_role, {"name": "role1"}, "/roles/role1", "DELETE"),
]


@pytest.mark.parametrize("tool,kwargs,endpoint_suffix,method", SIMPLE_DELETE_TOOLS)
def test_dry_run_never_calls_client(monkeypatch, tool, kwargs, endpoint_suffix, method):
    def fail_get_client():
        raise AssertionError(f"{tool.__name__}(dry_run=True) must not call get_client")

    monkeypatch.setattr(config, "get_client", fail_get_client)

    result = tool(**kwargs, dry_run=True)

    assert result["dry_run"] is True


@pytest.mark.parametrize("tool,kwargs,endpoint_suffix,method", SIMPLE_DELETE_TOOLS)
def test_delete_dispatches_correct_method_and_endpoint(monkeypatch, tool, kwargs, endpoint_suffix, method):
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"deleted": True}
    client._request.return_value = resp
    monkeypatch.setattr(config, "get_client", lambda: client)

    result = tool(**kwargs, dry_run=False)

    client._request.assert_called_once()
    args = client._request.call_args.args
    assert args[0] == method
    assert args[1].endswith(endpoint_suffix)
    assert result == {"deleted": True}


def test_delete_webhook_returns_status_code_and_body(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"deleted": True}
    client._request.return_value = resp
    monkeypatch.setattr(config, "get_client", lambda: client)

    result = config.delete_webhook("hook-1")

    client._request.assert_called_once_with("DELETE", f"{config._WEBHOOKS_BASE}/hook-1")
    assert result == {"status_code": 200, "response": {"deleted": True}}


def test_delete_device_groups_bulk_deletes_by_scope_id_list(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"deleted": 2}
    client._request.return_value = resp
    monkeypatch.setattr(config, "get_client", lambda: client)

    result = config.delete_device_groups(["s1", "s2"])

    client._request.assert_called_once_with(
        "DELETE",
        f"{config._DEVICE_GROUPS_BASE}/bulk",
        json={"items": [{"id": "s1"}, {"id": "s2"}]},
    )
    assert result == {"status_code": 200, "response": {"deleted": 2}}


# ---------------------------------------------------------------------------
# trigger_device_upgrade
# ---------------------------------------------------------------------------


def _mcp_client(device_type="ACCESS_POINT", scope_id="scope-1"):
    mcp_client = MagicMock()
    mcp_client.get_device_by_serial.return_value = {"deviceType": device_type} if device_type else None
    mcp_client.get_device_scope_id.return_value = scope_id
    return mcp_client


def test_trigger_device_upgrade_auto_detects_device_function_from_ap(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"accepted": True}
    client._request.return_value = resp
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "get_mcp_client", lambda: _mcp_client("ACCESS_POINT"))

    result = config.trigger_device_upgrade("AP1", "10.16.0.1")

    assert result["device_function"] == "CAMPUS_AP"
    assert result["errors"] == []
    assert result["response"] == {"accepted": True}


def test_trigger_device_upgrade_fails_structured_when_device_function_unresolvable(monkeypatch):
    def fail_get_client():
        raise AssertionError("must not build a client when device_function can't be resolved")

    monkeypatch.setattr(config, "get_client", fail_get_client)
    monkeypatch.setattr(config, "get_mcp_client", lambda: _mcp_client(device_type=None))

    result = config.trigger_device_upgrade("UNKNOWN1", "10.16.0.1")

    assert result["response"] is None
    assert "Could not determine device_function" in result["errors"][0]


def test_trigger_device_upgrade_fails_structured_when_scope_id_unresolvable(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(config, "get_client", lambda: client)
    mcp_client = _mcp_client("ACCESS_POINT", scope_id=None)
    monkeypatch.setattr(config, "get_mcp_client", lambda: mcp_client)

    result = config.trigger_device_upgrade("AP1", "10.16.0.1")

    client._request.assert_not_called()
    assert result["response"] is None
    assert "Could not resolve scope-id" in result["errors"][0]


def test_trigger_device_upgrade_dry_run_returns_payload_without_client_call(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "get_mcp_client", lambda: _mcp_client("ACCESS_POINT"))

    result = config.trigger_device_upgrade("AP1", "10.16.0.1", dry_run=True)

    client._request.assert_not_called()
    assert result["dry_run"] is True
    assert result["payload"]["version-chart"]["version"] == "10.16.0.1"


def test_trigger_device_upgrade_retries_with_patch_on_412(monkeypatch):
    client = MagicMock()
    post_resp = MagicMock(status_code=412)
    patch_resp = MagicMock(status_code=200)
    patch_resp.json.return_value = {"updated": True}
    client._request.side_effect = [post_resp, patch_resp]
    monkeypatch.setattr(config, "get_client", lambda: client)
    monkeypatch.setattr(config, "get_mcp_client", lambda: _mcp_client("ACCESS_POINT"))

    result = config.trigger_device_upgrade("AP1", "10.16.0.1")

    assert client._request.call_count == 2
    assert client._request.call_args_list[0].args[0] == "POST"
    assert client._request.call_args_list[1].args[0] == "PATCH"
    assert result["response"] == {"updated": True}
    assert result["errors"] == []
