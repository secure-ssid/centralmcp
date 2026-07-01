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
