"""Regression coverage for nac.py's irreversible delete_* tools.

None of these had any test coverage before this file. All follow the same
shape: dry_run short-circuits before touching the client, a successful
DELETE returns the parsed body untouched, and a failed DELETE appends a
compact error string under "errors" without raising.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_servers import nac

# (tool, id_kwarg, id_value, endpoint_suffix, supports_dry_run)
DELETE_TOOLS = [
    (nac.delete_mac_registration, "registration_id", "REG1", "/cnac-mac-reg/REG1", True),
    (nac.delete_mpsk_registration, "registration_id", "REG2", "/cnac-named-mpsk-reg/REG2", True),
    (nac.delete_visitor, "visitor_id", "VIS1", "/cnac-visitor/VIS1", True),
    (nac.delete_auth_server, "name", "radius-primary", "/auth-servers/radius-primary", True),
    (nac.delete_aaa_profile, "name", "aaa-default", "/aaa-profile/aaa-default", True),
    (nac.delete_authz_policy, "policy_id", "POL1", "/authz-policies/POL1", True),
    (nac.delete_static_tag, "tag_id", "TAG1", "/static-tag/TAG1", False),
    (nac.delete_auth_profile, "profile_id", "PROF1", "/auth-profiles/PROF1", True),
]


@pytest.mark.parametrize("tool,id_kwarg,id_value,endpoint_suffix,supports_dry_run", DELETE_TOOLS)
def test_dry_run_never_calls_client(monkeypatch, tool, id_kwarg, id_value, endpoint_suffix, supports_dry_run):
    if not supports_dry_run:
        pytest.skip(f"{tool.__name__} has no dry_run parameter")

    def fail_get_client():
        raise AssertionError(f"{tool.__name__}(dry_run=True) must not call get_client")

    monkeypatch.setattr(nac, "get_client", fail_get_client)

    result = tool(**{id_kwarg: id_value}, dry_run=True)

    assert result["dry_run"] is True


@pytest.mark.parametrize("tool,id_kwarg,id_value,endpoint_suffix,supports_dry_run", DELETE_TOOLS)
def test_successful_delete_returns_body_without_errors(monkeypatch, tool, id_kwarg, id_value, endpoint_suffix, supports_dry_run):
    client = MagicMock()
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = {"deleted": True}
    client._request.return_value = resp
    monkeypatch.setattr(nac, "get_client", lambda: client)

    kwargs = {id_kwarg: id_value}
    if supports_dry_run:
        kwargs["dry_run"] = False
    result = tool(**kwargs)

    client._request.assert_called_once()
    method, endpoint = client._request.call_args.args
    assert method == "DELETE"
    assert endpoint.endswith(endpoint_suffix)
    assert result.get("deleted") is True
    assert "errors" not in result


@pytest.mark.parametrize("tool,id_kwarg,id_value,endpoint_suffix,supports_dry_run", DELETE_TOOLS)
def test_failed_delete_reports_errors_without_raising(monkeypatch, tool, id_kwarg, id_value, endpoint_suffix, supports_dry_run):
    client = MagicMock()
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = 404
    resp.json.return_value = {"message": "not found"}
    client._request.return_value = resp
    monkeypatch.setattr(nac, "get_client", lambda: client)

    kwargs = {id_kwarg: id_value}
    if supports_dry_run:
        kwargs["dry_run"] = False
    result = tool(**kwargs)

    assert result["errors"]
    assert "404" in result["errors"][0]
