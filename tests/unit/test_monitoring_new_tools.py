from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mcp_servers import monitoring


def _response(status_code: int = 202, payload: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.side_effect = lambda: dict(payload or {})
    response.text = "{}"
    return response


class _AcceptCtx:
    async def elicit(self, message, schema):
        return SimpleNamespace(action="accept", data=schema(confirm=True))


class _DeclineCtx:
    async def elicit(self, message, schema):
        return SimpleNamespace(action="decline", data=schema(confirm=False))


def test_list_active_alerts_calls_expected_endpoint(monkeypatch):
    client = MagicMock()
    client.get.return_value = {"items": []}
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.list_active_alerts(
        site_id="site-1",
        severity="CRITICAL",
        limit=25,
        offset=10,
    )

    assert result == {"items": []}
    client.get.assert_called_once_with(
        "/network-notifications/v1/alerts",
        params={
            "limit": 25,
            "offset": 10,
            "filter": "status eq 'Active' and siteId eq 'site-1' and severity eq 'CRITICAL'",
            "sort": "severity desc",
        },
    )


def test_legacy_list_alerts_forwards_offset(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_alerts.return_value = []
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.list_alerts(severity="CRITICAL", limit=25, offset=-10)

    assert result == []
    mcp_client.get_alerts.assert_called_once_with(
        site_id=None,
        severity="CRITICAL",
        limit=25,
        offset=0,
    )


def test_list_clients_forwards_offset(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_clients.return_value = []
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.list_clients(site_id="site-1", limit=25, offset=10)

    assert result == []
    mcp_client.get_clients.assert_called_once_with(
        site_id="site-1",
        serial_number=None,
        ssid=None,
        connection_type=None,
        limit=25,
        offset=10,
    )


def test_list_alert_classifications_calls_expected_endpoint(monkeypatch):
    client = MagicMock()
    client.get.return_value = {"Critical": 2}
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.list_alert_classifications(
        classify_by="severity",
        filter="status eq 'Active'",
        search="uplink",
    )

    assert result == {"Critical": 2}
    client.get.assert_called_once_with(
        "/network-notifications/v1/alerts/classification",
        params={"type": "severity", "filter": "status eq 'Active'", "search": "uplink"},
    )


def test_list_alert_configs_calls_expected_endpoint(monkeypatch):
    client = MagicMock()
    client.get.return_value = {"items": []}
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.list_alert_configs(scope_id=" global-scope ", scope_type="global")

    assert result == {"items": []}
    client.get.assert_called_once_with(
        "/network-notifications/v1/alert-config",
        params={"scopeId": "global-scope", "scopeType": "GLOBAL"},
    )


def test_list_alert_configs_validates_scope_type(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    with pytest.raises(ValueError, match="scope_type must be one of"):
        monitoring.list_alert_configs(scope_id="scope-1", scope_type="GROUP")

    client.get.assert_not_called()


def test_list_insights_calls_expected_endpoint(monkeypatch):
    client = MagicMock()
    client.get.return_value = {"items": []}
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.list_insights(
        filter="severity eq 'HIGH'",
        limit=500,
        offset=5,
    )

    assert result == {"items": []}
    client.get.assert_called_once_with(
        "/network-notifications/v1/insights",
        params={"limit": 200, "offset": 5, "filter": "severity eq 'HIGH'"},
    )


def test_get_tenant_health_collects_both_summaries(monkeypatch):
    client = MagicMock()
    client.get.side_effect = [{"score": 99}, {"score": 88}]
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.get_tenant_health()

    assert result["device_health"] == {"score": 99}
    assert result["client_health"] == {"score": 88}
    assert result["errors"] == []
    assert client.get.call_count == 2


def test_get_alert_action_status_uses_quoted_task_path(monkeypatch):
    client = MagicMock()
    client._request.return_value = _response(200, {"status": "COMPLETED"})
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = monitoring.get_alert_action_status("task/123")

    assert result == {
        "status": "COMPLETED",
        "endpoint_used": "/network-notifications/v1/alerts/async-operations/task%2F123",
    }
    client._request.assert_called_once_with(
        "GET",
        "/network-notifications/v1/alerts/async-operations/task%2F123",
    )


def test_clear_alerts_confirms_then_posts_expected_payload(monkeypatch):
    client = MagicMock()
    client._request.return_value = _response(202, {"task_id": "task-1"})
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = asyncio.run(
        monitoring.clear_alerts(
            _AcceptCtx(),
            keys=[" alert-1 ", "alert-2"],
            reason="Problem was resolved",
            notes="fixed upstream",
        )
    )

    assert result == {
        "task_id": "task-1",
        "endpoint_used": "/network-notifications/v1/alerts/clear",
    }
    client._request.assert_called_once_with(
        "POST",
        "/network-notifications/v1/alerts/clear",
        json={
            "keys": ["alert-1", "alert-2"],
            "reason": "Problem was resolved",
            "notes": "fixed upstream",
        },
    )


def test_alert_actions_reject_empty_keys():
    with pytest.raises(ValueError, match="keys must contain"):
        asyncio.run(monitoring.reactivate_alerts(_AcceptCtx(), keys=[]))


def test_set_alert_priority_validates_priority(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    with pytest.raises(ValueError, match="priority must be one of"):
        asyncio.run(monitoring.set_alert_priority(_AcceptCtx(), keys=["alert-1"], priority="Urgent"))

    client._request.assert_not_called()


def test_alert_action_decline_does_not_post(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    result = asyncio.run(
        monitoring.set_alert_priority(_DeclineCtx(), keys=["alert-1"], priority="Low")
    )

    assert result == {"status": "CANCELLED", "detail": "user declined confirmation"}
    client._request.assert_not_called()


def test_defer_and_reactivate_alerts_confirm_then_post_expected_payloads(monkeypatch):
    client = MagicMock()
    client._request.return_value = _response(202, {"task_id": "task-2"})
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    deferred = asyncio.run(
        monitoring.defer_alerts(
            _AcceptCtx(),
            keys=["alert-1"],
            defer_until="2026-07-01T10:00:00Z",
        )
    )
    reactivated = asyncio.run(monitoring.reactivate_alerts(_AcceptCtx(), keys=["alert-1"]))

    assert deferred["endpoint_used"] == "/network-notifications/v1/alerts/defer"
    assert reactivated["endpoint_used"] == "/network-notifications/v1/alerts/active"
    assert client._request.call_args_list[0].args == (
        "POST",
        "/network-notifications/v1/alerts/defer",
    )
    assert client._request.call_args_list[0].kwargs == {
        "json": {"keys": ["alert-1"], "deferUntil": "2026-07-01T10:00:00Z"}
    }
    assert client._request.call_args_list[1].args == (
        "POST",
        "/network-notifications/v1/alerts/active",
    )
    assert client._request.call_args_list[1].kwargs == {"json": {"keys": ["alert-1"]}}


def test_config_health_tools_call_expected_endpoints(monkeypatch):
    client = MagicMock()
    client.get.side_effect = [{"issues": []}, {"items": []}]
    client.post.return_value = {"message": "Full configuration sync triggered for 1 devices."}
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    issues = monitoring.get_device_config_issues(" SN123 ")
    health = monitoring.list_devices_config_health(
        limit=500,
        offset=5,
        sort="activeIssues desc",
        filter="configStatus eq 'OUT_OF_SYNC'",
        search="SN1",
    )
    resync = monitoring.resync_device_config([" SN123 "])

    assert issues == {"issues": []}
    assert health == {"items": []}
    assert resync == {"message": "Full configuration sync triggered for 1 devices."}
    client.get.assert_any_call(
        "/network-config/v1alpha1/config-health/active-issue",
        params={"serial": "SN123"},
    )
    client.get.assert_any_call(
        "/network-config/v1alpha1/config-health/devices",
        params={
            "limit": 200,
            "offset": 5,
            "sort": "activeIssues desc",
            "filter": "configStatus eq 'OUT_OF_SYNC'",
            "search": "SN1",
        },
    )
    client.post.assert_called_once_with(
        "/network-config/v1alpha1/config-health/devices-resync",
        data={"serials": ["SN123"]},
    )


def test_find_scope_matches_name_and_type(monkeypatch):
    monkeypatch.setattr(
        monitoring,
        "list_scopes",
        lambda full_list=False: {
            "items": [
                {"scope_id": "global", "scope_name": "Global", "scope_type": "GLOBAL"},
                {"scope_id": "site-1", "scope_name": "Austin Lab", "scope_type": "SITE"},
            ]
        },
    )

    result = monitoring.find_scope("austin", scope_type="site")

    assert result["items"] == [
        {
            "scope_id": "site-1",
            "scope_name": "Austin Lab",
            "scope_type": "SITE",
            "raw": {"scope_id": "site-1", "scope_name": "Austin Lab", "scope_type": "SITE"},
        }
    ]


def test_list_scope_devices_filters_known_scope_fields(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_devices.return_value = [
        {"serialNumber": "AP1", "siteId": "site-1", "deviceType": "ACCESS_POINT"},
        {"serialNumber": "SW1", "siteId": "site-1", "deviceType": "SWITCH"},
        {"serialNumber": "AP2", "siteId": "site-2", "deviceType": "ACCESS_POINT"},
    ]
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.list_scope_devices("site-1", device_type="AP", limit=10)

    assert result["items"] == [
        {"serialNumber": "AP1", "siteId": "site-1", "deviceType": "ACCESS_POINT"}
    ]
    mcp_client.get_devices.assert_called_once_with({"siteId": "site-1"}, limit=200, offset=0)


def test_site_health_summary_uses_site_id_inventory_filter(monkeypatch):
    mcp_client = MagicMock()
    mcp_client.get_devices.return_value = [
        {"serialNumber": "SW1", "siteId": "site-1", "deviceType": "SWITCH", "status": "UP"}
    ]
    mcp_client.get_clients.return_value = []
    mcp_client.get_alerts.return_value = []
    mcp_client.get_events.return_value = []
    monkeypatch.setattr(monitoring, "get_mcp_client", lambda: mcp_client)

    result = monitoring.get_site_health_summary(site_id="site-1")

    assert result["site_id"] == "site-1"
    mcp_client.get_devices.assert_called_once_with(filters={"siteId": "site-1"}, limit=200)


def test_list_devices_config_health_validates_search_length(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(monitoring, "get_client", lambda: client)

    with pytest.raises(ValueError, match="search must be"):
        monitoring.list_devices_config_health(search="ab")

    client.get.assert_not_called()
