from __future__ import annotations

import json

from mcp_servers._middleware.audit_log import AuditLogMiddleware, audit_path


def test_audit_disabled_without_environment(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_AUDIT_LOG", raising=False)

    assert audit_path() is None


def test_audit_records_router_target_without_raw_arguments(tmp_path):
    path = tmp_path / "audit.jsonl"
    middleware = AuditLogMiddleware(path)
    arguments = {
        "name": "create_ssid",
        "arguments": {
            "ssid": "employee",
            "password": "SuperSecret!",
            "token": "Bearer secret-token",
        },
    }

    middleware.before_call("invoke_tool", arguments)
    middleware.after_call("invoke_tool", arguments, {"status": "blocked"})

    text = path.read_text()
    record = json.loads(text)
    assert record["tool"] == "invoke_tool"
    assert record["target_tool"] == "create_ssid"
    assert record["outcome"] == "blocked"
    assert record["argument_keys"] == ["arguments", "name"]
    assert len(record["argument_digest"]) == 64
    assert record["duration_ms"] is not None
    assert "SuperSecret" not in text
    assert "secret-token" not in text
    assert "employee" not in text


def test_audit_records_exception_type_without_message(tmp_path):
    path = tmp_path / "audit.jsonl"
    middleware = AuditLogMiddleware(path)
    arguments = {"name": "delete_vlan", "arguments": {"password": "do-not-log"}}

    middleware.before_call("invoke_tool", arguments)
    middleware.on_error(
        "invoke_tool",
        arguments,
        RuntimeError("failure contains do-not-log"),
    )

    text = path.read_text()
    record = json.loads(text)
    assert record["outcome"] == "exception"
    assert record["error_type"] == "RuntimeError"
    assert "do-not-log" not in text
