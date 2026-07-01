from __future__ import annotations

from mcp_servers import glp


def test_glp_write_status_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_GLP_V2BETA1_WRITES", raising=False)

    status = glp.glp_write_status()

    assert status["enabled"] is False
    assert status["flag"] == "CENTRALMCP_GLP_V2BETA1_WRITES"
    assert "glp_archive_device" in status["guarded_tools"]
    assert "fail closed" in status["message"]


def test_glp_write_status_enabled(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_GLP_V2BETA1_WRITES", "1")

    status = glp.glp_write_status()

    assert status["enabled"] is True
    assert "can execute" in status["message"]


def test_glp_get_rejects_absolute_url():
    result = glp.glp_get("https://evil.example/devices/v1/devices")

    assert "Invalid path" in result["error"]


def test_glp_get_rejects_dot_segments():
    result = glp.glp_get("/service-catalog/v1/../devices")

    assert "dot segments" in result["error"]


def test_glp_get_rejects_unsupported_prefix():
    result = glp.glp_get("/admin/v1/secrets")

    assert "path must begin" in result["error"]


def test_glp_get_calls_guarded_path(monkeypatch):
    class DummyCentral:
        def get(self, path, params=None):
            return {"path": path, "params": params}

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    result = glp.glp_get("/service-catalog/v1/services", {"limit": 5})

    assert result == {
        "data": {"path": "/service-catalog/v1/services", "params": {"limit": 5}},
        "endpoint_used": "/service-catalog/v1/services",
    }


def test_glp_get_accepts_official_audit_log_prefix(monkeypatch):
    class DummyCentral:
        def get(self, path, params=None):
            return {"path": path, "params": params}

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    result = glp.glp_get("/audit-log/v1/logs", {"limit": 5})

    assert result == {
        "data": {"path": "/audit-log/v1/logs", "params": {"limit": 5}},
        "endpoint_used": "/audit-log/v1/logs",
    }


def test_glp_get_bounds_list_payloads(monkeypatch):
    class DummyCentral:
        def get(self, path, params=None):
            return [{"id": 1}, {"id": 2}, {"id": 3}]

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    result = glp.glp_get("/service-catalog/v1/services", limit=2, offset=1)

    assert result == {
        "data": {
            "items": [{"id": 2}, {"id": 3}],
            "_pagination": {
                "offset": 1,
                "limit": 2,
                "total": 3,
                "truncated": False,
            },
        },
        "endpoint_used": "/service-catalog/v1/services",
    }


def test_glp_list_tools_clamp_limit_and_forward_offset(monkeypatch):
    calls = []

    class DummyGLP:
        def list_devices(self, limit=100, offset=0, filter=None):
            calls.append(("devices", limit, offset, filter))
            return []

        def list_subscriptions(self, limit=100, offset=0):
            calls.append(("subscriptions", limit, offset))
            return []

        def list_users(self, limit=100, offset=0):
            calls.append(("users", limit, offset))
            return []

        def list_audit_logs(self, limit=100, offset=0, category=None):
            calls.append(("audit", limit, offset, category))
            return []

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    assert glp.list_glp_devices(limit=999, offset=-1, filter="deviceType eq 'AP'")["errors"] == []
    assert glp.list_glp_subscriptions(limit=999, offset=2)["errors"] == []
    assert glp.list_glp_users(limit=999, offset=3)["errors"] == []
    assert glp.list_glp_audit_logs(limit=999, offset=4, category="USER_MANAGEMENT")["errors"] == []
    assert calls == [
        ("devices", 200, 0, "deviceType eq 'AP'"),
        ("subscriptions", 200, 2),
        ("users", 200, 3),
        ("audit", 200, 4, "USER_MANAGEMENT"),
    ]


def test_glp_official_id_wrappers_encode_and_call_paths(monkeypatch):
    calls = []

    class DummyCentral:
        def get(self, path, params=None):
            calls.append((path, params))
            return {"path": path, "params": params}

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    assert glp.get_glp_device_by_id("device 1")["data"]["path"] == "/devices/v1/devices/device%201"
    assert glp.get_glp_audit_log_detail("audit-1")["data"]["path"] == (
        "/audit-log/v1/logs/audit-1/detail"
    )
    assert glp.get_glp_user("user 1")["data"]["path"] == "/identity/v1/users/user%201"
    assert glp.get_glp_workspace("workspace-1")["data"]["path"] == (
        "/workspaces/v1/workspaces/workspace-1"
    )
    assert glp.get_glp_reporting_status("report-1")["data"]["path"] == (
        "/reporting/v1/statuses/report-1"
    )
    assert calls == [
        ("/devices/v1/devices/device%201", {}),
        ("/audit-log/v1/logs/audit-1/detail", {}),
        ("/identity/v1/users/user%201", {}),
        ("/workspaces/v1/workspaces/workspace-1", {}),
        ("/reporting/v1/statuses/report-1", {}),
    ]


def test_glp_official_list_wrappers_clamp_and_forward_params(monkeypatch):
    calls = []

    class DummyCentral:
        def get(self, path, params=None):
            calls.append((path, params))
            return {"items": []}

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    assert glp.list_glp_reporting_statuses(
        filter="type eq 'REPORT'",
        sort="name asc",
        limit=999,
        offset=-2,
    )["errors"] == []
    assert glp.list_glp_service_offers(
        next_cursor="cursor-1",
        limit=999,
        filter="status eq 'ONBOARDED'",
    )["errors"] == []
    assert glp.list_glp_service_manager_provisions(limit=999, offset=3)["errors"] == []

    assert calls == [
        (
            "/reporting/v1/statuses",
            {"filter": "type eq 'REPORT'", "sort": "name asc", "limit": 200, "offset": 0},
        ),
        (
            "/service-catalog/v1beta1/service-offers",
            {"next": "cursor-1", "filter": "status eq 'ONBOARDED'", "limit": 200},
        ),
        (
            "/service-catalog/v1/service-manager-provisions",
            {"limit": 200, "offset": 3},
        ),
    ]


def test_glp_service_provisions_can_send_workspace_header(monkeypatch):
    calls = []

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": []}

    class DummyCentral:
        def _request(self, method, path, params=None, headers=None):
            calls.append((method, path, params, headers))
            return DummyResponse()

    class DummyGLP:
        _client = DummyCentral()

    monkeypatch.setattr(glp, "get_glp_client", lambda: DummyGLP())

    result = glp.list_glp_service_provisions(
        workspace_id="workspace-1",
        next_cursor="cursor-1",
        limit=999,
        filter="slug eq 'AC'",
        unredacted=True,
        all_workspaces=False,
    )

    assert result["errors"] == []
    assert result["data"] == {"items": []}
    assert calls == [
        (
            "GET",
            "/service-catalog/v1beta1/service-provisions",
            {
                "next": "cursor-1",
                "filter": "slug eq 'AC'",
                "unredacted": True,
                "all": False,
                "limit": 200,
            },
            {"Hpe-workspace-id": "workspace-1"},
        )
    ]


def test_glp_add_device_fails_closed_when_writes_disabled(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_GLP_V2BETA1_WRITES", raising=False)

    def fail_client():
        raise AssertionError("get_glp_client should not be called when writes are disabled")

    monkeypatch.setattr(glp, "get_glp_client", fail_client)

    result = glp.glp_add_device("SERIAL1", "aa:bb:cc:dd:ee:ff")

    assert result["status"] == "FORBIDDEN"
    assert "CENTRALMCP_GLP_V2BETA1_WRITES=1" in result["error"]
    assert result["would_have_sent"]["serial_number"] == "SERIAL1"


def test_glp_assign_subscription_fails_closed_when_writes_disabled(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_GLP_V2BETA1_WRITES", raising=False)

    result = glp.glp_assign_subscription("SERIAL1", "SUBKEY")

    assert result["status"] == "FORBIDDEN"
    assert result["would_have_sent"] == {
        "serial_number": "SERIAL1",
        "subscription_key": "SUBKEY",
    }
