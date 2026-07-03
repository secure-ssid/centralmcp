from unittest.mock import MagicMock

from pipeline.clients.mcp_client import MCPClient


def test_get_device_scope_id_uses_central_client_get():
    central = MagicMock()
    central.get.return_value = {"items": [{"scopeId": "scope-1"}]}

    assert MCPClient(central).get_device_scope_id("CN123") == "scope-1"
    central.get.assert_called_once_with(
        "/network-config/v1alpha1/devices",
        params={"filter": "scopeName eq 'CN123'"},
    )
    assert not central.session.get.called


def test_get_device_scope_id_returns_none_for_empty_result():
    central = MagicMock()
    central.get.return_value = {"items": []}

    assert MCPClient(central).get_device_scope_id("CN123") is None


def test_get_device_scope_id_returns_none_on_client_error():
    central = MagicMock()
    central.get.side_effect = RuntimeError("boom")

    assert MCPClient(central).get_device_scope_id("CN123") is None


def test_get_sites_applies_client_side_limit_and_offset():
    central = MagicMock()
    central.get.return_value = {
        "items": [
            {"id": "site-1"},
            {"id": "site-2"},
            {"id": "site-3"},
        ]
    }

    assert MCPClient(central).get_sites(limit=1, offset=1) == [{"id": "site-2"}]
    central.get.assert_called_once_with("/network-config/v1/sites")


def test_get_sites_clamps_negative_offset_and_large_limit():
    central = MagicMock()
    central.get.return_value = {"sites": [{"id": f"site-{idx}"} for idx in range(250)]}

    sites = MCPClient(central).get_sites(limit=999, offset=-10)

    assert len(sites) == 200
    assert sites[0] == {"id": "site-0"}


def test_get_clients_sends_bounded_limit_and_offset():
    central = MagicMock()
    central.get.return_value = {"items": []}

    assert MCPClient(central).get_clients(site_id="site-1", limit=999, offset=-5) == []
    central.get.assert_called_once_with(
        "/network-monitoring/v1/clients",
        params={"limit": 200, "offset": 0, "site-id": "site-1"},
    )


def test_get_alerts_sends_bounded_limit_and_offset():
    central = MagicMock()
    central.get.return_value = {"items": []}

    assert MCPClient(central).get_alerts(severity="critical", limit=999, offset=25) == []
    central.get.assert_called_once_with(
        "/network-notifications/v1/alerts",
        params={
            "filter": "status eq 'Active' and severity eq 'Critical'",
            "limit": 200,
            "offset": 25,
        },
    )


def test_get_site_by_name_searches_past_the_default_page_limit():
    # Regression: get_site_by_name used the paged get_sites() default (50),
    # so site #51+ was reported as nonexistent — breaking migration
    # validation and post-create lookup on tenants with >50 sites.
    central = MagicMock()
    central.get.return_value = {
        "items": [{"scopeName": f"Site-{i}", "id": f"s{i}"} for i in range(60)]
    }

    found = MCPClient(central).get_site_by_name("Site-59")

    assert found == {"scopeName": "Site-59", "id": "s59"}


def test_get_device_by_serial_finds_device_beyond_old_thousand_item_cap():
    central = MagicMock()

    def fake_get(endpoint, params=None):
        offset = params["offset"]
        limit = params["limit"]
        devices = [
            {"serialNumber": f"SN{i}"} for i in range(offset, min(offset + limit, 1500))
        ]
        return {"devices": devices}

    central.get.side_effect = fake_get

    found = MCPClient(central).get_device_by_serial("SN1400")

    assert found == {"serialNumber": "SN1400"}
