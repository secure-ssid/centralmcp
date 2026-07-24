"""Production-path regression tests for the AOS8 MCP boundary's Classic
Central target resolution and status-aware preflight-read translation.

These tests exercise the *real* `mcp_servers.aos8` functions (not the
FakeBackend-based pure adapter tests in `test_aos8_target_adapters.py`):

- `_aos8_migration_read_invoker` must translate a production
  `CentralClient.get()` `httpx.HTTPStatusError` (which is raised on *every*
  non-2xx response, including a normal "this item does not exist yet" 404)
  into a status-carrying `ReadStatusError` instead of losing the status
  code.
- `_aos8_migration_classic_target_resolver` must resolve Classic Central
  targets directly from the caller-declared `scope_name`, and must never
  call the New Central `/scopes` lookup (`list_scopes`/`get_global_scope_id`)
  that `_aos8_migration_scope_resolver` uses.
"""

from __future__ import annotations

import httpx
import pytest

import mcp_servers.aos8 as aos8
from pipeline.aos8_target_adapters import (
    ClassicCentralAdapter,
    Operation,
    ReadStatusError,
    TargetContext,
    TargetType,
)


class _FakeCentralClient:
    """Stands in for `pipeline.clients.central_client.CentralClient`, whose
    production `.get()` calls `response.raise_for_status()` and therefore
    raises `httpx.HTTPStatusError` on any non-2xx status -- including a
    normal, expected 404 for "not found yet"."""

    def __init__(self, status_code: int):
        self._status_code = status_code

    def get(self, endpoint: str):
        request = httpx.Request("GET", f"https://example.invalid{endpoint}")
        response = httpx.Response(self._status_code, request=request)
        if not 200 <= self._status_code < 300:
            raise httpx.HTTPStatusError(
                f"HTTP {self._status_code}", request=request, response=response
            )
        return response


def _read_operation(endpoint: str = "/configuration/full_wlan/Branch/Guest") -> Operation:
    return Operation(
        invocation="endpoint",
        name="central_api_read",
        arguments={},
        method="GET",
        endpoint=endpoint,
        match_identifier="Guest",
    )


# --------------------------------------------------------------------------
# Finding #1: production-path status-aware preflight read
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [404, 401, 403, 500, 503])
def test_read_invoker_translates_production_http_status_error(monkeypatch, status_code):
    monkeypatch.setattr(aos8, "get_client", lambda: _FakeCentralClient(status_code))
    with pytest.raises(ReadStatusError) as excinfo:
        aos8._aos8_migration_read_invoker(_read_operation())
    assert excinfo.value.status_code == status_code


def test_read_invoker_returns_response_on_success(monkeypatch):
    monkeypatch.setattr(aos8, "get_client", lambda: _FakeCentralClient(200))
    response = aos8._aos8_migration_read_invoker(_read_operation())
    assert response.status_code == 200


def _classic_context(scope_name: str) -> TargetContext:
    return TargetContext(
        target_type=TargetType.CLASSIC_CENTRAL,
        scope_id=None,
        scope_name=scope_name,
        persona="CAMPUS_AP",
    )


def test_read_invoker_404_classifies_as_absent_end_to_end(monkeypatch):
    # End-to-end: production read invoker + real ClassicCentralAdapter +
    # real Classic target resolver, wired the same way
    # `_aos8_migration_orchestrator()` wires them, confirming a 404 preflight
    # read is treated as "safe to create" (not "blocked").
    monkeypatch.setattr(aos8, "get_client", lambda: _FakeCentralClient(404))
    adapter = ClassicCentralAdapter(
        _classic_context("Branch Group"),
        scope_resolver=aos8._aos8_migration_classic_target_resolver,
        persona_validator=aos8._aos8_migration_persona_validator,
        read_invoker=aos8._aos8_migration_read_invoker,
        write_invoker=aos8._aos8_migration_write_invoker,
        writes_enabled=lambda _target: True,
    )
    wlan = {
        "object_type": "wlan",
        "identifier": "Guest",
        "payload": {
            "name": "Guest",
            "essid": "Guest",
            "vlan": 20,
            "aaa_profile": None,
            "security": {
                "mode": "open",
                "opmode": "open",
                "ambiguous": False,
                "aaa_profile": None,
                "dot1x_auth_profile": None,
                "mac_auth_profile": None,
                "passphrase_present": False,
                "psk_hexkey_present": False,
                "wpa3_transition": False,
                "evidence": [],
            },
        },
        "dependencies": [],
        "apply_order": 10,
        "unsupported_fields": {
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
        "requires_secret_input": False,
        "secret_fields": [],
        "warnings": [],
    }
    preview = adapter.preview([wlan])
    action = preview["operations"][0]
    assert action["status"] == "ready"
    assert action["conflict"] == "absent"
    assert action["operations"][0]["method"] == "POST"


def test_read_invoker_401_classifies_as_unsupported_end_to_end(monkeypatch):
    monkeypatch.setattr(aos8, "get_client", lambda: _FakeCentralClient(401))
    adapter = ClassicCentralAdapter(
        _classic_context("Branch Group"),
        scope_resolver=aos8._aos8_migration_classic_target_resolver,
        persona_validator=aos8._aos8_migration_persona_validator,
        read_invoker=aos8._aos8_migration_read_invoker,
        write_invoker=aos8._aos8_migration_write_invoker,
        writes_enabled=lambda _target: True,
    )
    wlan = {
        "object_type": "wlan",
        "identifier": "Guest",
        "payload": {
            "name": "Guest",
            "essid": "Guest",
            "vlan": 20,
            "aaa_profile": None,
            "security": {
                "mode": "open",
                "opmode": "open",
                "ambiguous": False,
                "aaa_profile": None,
                "dot1x_auth_profile": None,
                "mac_auth_profile": None,
                "passphrase_present": False,
                "psk_hexkey_present": False,
                "wpa3_transition": False,
                "evidence": [],
            },
        },
        "dependencies": [],
        "apply_order": 10,
        "unsupported_fields": {
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
        "requires_secret_input": False,
        "secret_fields": [],
        "warnings": [],
    }
    preview = adapter.preview([wlan])
    action = preview["operations"][0]
    assert action["status"] == "unsupported"
    assert "401" in action["unsupported_warnings"][0]


# --------------------------------------------------------------------------
# Finding #4: dedicated Classic target resolver, isolated from New Central
# `/scopes` lookups
# --------------------------------------------------------------------------


def _forbid_scopes_lookup(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError(
            "New Central /scopes lookup must never be called for a Classic "
            "Central target."
        )

    monkeypatch.setattr("mcp_servers.monitoring.list_scopes", _boom)
    monkeypatch.setattr("mcp_servers.monitoring.get_global_scope_id", _boom)


@pytest.mark.parametrize(
    "scope_name",
    ["Branch Group", "12345", "550e8400-e29b-41d4-a716-446655440000", "CN12345678"],
)
def test_classic_target_resolver_never_calls_new_central_scopes_api(
    monkeypatch, scope_name
):
    _forbid_scopes_lookup(monkeypatch)
    context = _classic_context(scope_name)
    scope_id, resolved_name = aos8._aos8_migration_classic_target_resolver(context)
    assert resolved_name == scope_name
    assert scope_id  # falls back to scope_name when scope_id is unset


def test_classic_target_resolver_rejects_missing_scope_name(monkeypatch):
    _forbid_scopes_lookup(monkeypatch)
    context = _classic_context("")
    with pytest.raises(ValueError, match="explicit scope_name"):
        aos8._aos8_migration_classic_target_resolver(context)


def test_classic_target_resolver_does_not_infer_from_bare_scope_id(monkeypatch):
    # A caller must never be able to feed a New Central scope_id into the
    # Classic path implicitly -- only an explicitly declared scope_name is
    # accepted as the Classic target string.
    _forbid_scopes_lookup(monkeypatch)
    context = TargetContext(
        target_type=TargetType.CLASSIC_CENTRAL,
        scope_id="99999",
        scope_name=None,
        persona="CAMPUS_AP",
    )
    with pytest.raises(ValueError, match="explicit scope_name"):
        aos8._aos8_migration_classic_target_resolver(context)


def test_adapter_factory_selects_classic_resolver_for_classic_target(monkeypatch):
    _forbid_scopes_lookup(monkeypatch)
    service = aos8._aos8_migration_orchestrator()
    adapter = service.adapter_factory(_classic_context("12345"))
    assert adapter.context.scope_name == "12345"


def test_adapter_factory_selects_new_central_resolver_for_new_central_target(
    monkeypatch,
):
    calls = {"count": 0}

    def fake_list_scopes(full_list=True):
        calls["count"] += 1
        return {"items": [{"scope_id": "100", "scope_name": "Branch"}]}

    monkeypatch.setattr("mcp_servers.monitoring.list_scopes", fake_list_scopes)
    service = aos8._aos8_migration_orchestrator()
    context = TargetContext(
        target_type=TargetType.NEW_CENTRAL,
        scope_id="100",
        scope_name="Branch",
        persona="CAMPUS_AP",
    )
    adapter = service.adapter_factory(context)
    assert adapter.context.scope_name == "Branch"
    assert calls["count"] == 1
