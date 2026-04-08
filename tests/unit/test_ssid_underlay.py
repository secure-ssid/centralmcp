"""Unit tests for pipeline/ssid_underlay.py."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from pipeline.ssid_underlay import (
    _build_ssid_body,
    build_underlay_ssid,
    delete_underlay_ssid,
    get_underlay_ssid,
    list_underlay_ssids,
)


# ---------------------------------------------------------------------------
# _build_ssid_body
# ---------------------------------------------------------------------------


def test_build_ssid_body_forward_mode():
    body = _build_ssid_body("TestSSID", ["1000"])
    assert body["forward-mode"] == "FORWARD_MODE_BRIDGE"


def test_build_ssid_body_vlan_selector():
    body = _build_ssid_body("TestSSID", ["1000", "1001"])
    assert body["vlan-selector"] == "VLAN_RANGES"
    assert body["vlan-id-range"] == ["1000", "1001"]


def test_build_ssid_body_essid_matches_ssid():
    body = _build_ssid_body("My SSID", ["200"])
    assert body["ssid"] == "My SSID"
    assert body["essid"]["name"] == "My SSID"


def test_build_ssid_body_defaults():
    body = _build_ssid_body("X", ["1"])
    assert body["enable"] is True
    assert body["opmode"] == "ENHANCED_OPEN"
    assert body["hide-ssid"] is False
    assert body["client-isolation"] is False
    assert body["high-efficiency"]["enable"] is True


def test_build_ssid_body_custom_opmode():
    body = _build_ssid_body("X", ["1"], opmode="WPA3_SAE")
    assert body["opmode"] == "WPA3_SAE"


def test_build_ssid_body_wpa3_passphrase():
    body = _build_ssid_body("X", ["1"], opmode="WPA3_SAE", wpa_passphrase="ilikeelephants")
    assert body["personal-security"]["wpa-passphrase"] == "ilikeelephants"
    assert body["personal-security"]["passphrase-format"] == "STRING"


def test_build_ssid_body_wpa2_passphrase():
    body = _build_ssid_body("X", ["1"], opmode="WPA2_PSK", wpa_passphrase="mypassword")
    assert "personal-security" in body


def test_build_ssid_body_no_passphrase_for_open():
    """ENHANCED_OPEN should never include personal-security even if passphrase passed."""
    body = _build_ssid_body("X", ["1"], opmode="ENHANCED_OPEN", wpa_passphrase="ignored")
    assert "personal-security" not in body


# ---------------------------------------------------------------------------
# build_underlay_ssid — dry-run
# ---------------------------------------------------------------------------


def test_build_underlay_ssid_dry_run_no_api_calls():
    client = MagicMock()
    result = build_underlay_ssid(client, "Test", ["1000"], "99999", dry_run=True)
    client.post.assert_not_called()
    assert result["created"] is True
    assert result["scope_mapped"] is True
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# build_underlay_ssid — happy path
# ---------------------------------------------------------------------------


def test_build_underlay_ssid_creates_and_maps():
    client = MagicMock()
    client.post.return_value = {"errorCode": "SUCC_001"}

    result = build_underlay_ssid(client, "Corp-WiFi", ["1000"], "79236221864456192")

    assert result["created"] is True
    assert result["scope_mapped"] is True
    assert result["errors"] == []

    # Step 2: create SSID
    first_call = client.post.call_args_list[0]
    assert first_call == call(
        "/network-config/v1/wlan-ssids/Corp-WiFi",
        data={
            **_build_ssid_body("Corp-WiFi", ["1000"]),
        },
    )

    # Step 3: scope-map
    second_call = client.post.call_args_list[1]
    assert second_call == call(
        "/network-config/v1/scope-maps",
        data={
            "scope-map": [
                {
                    "scope-name": "79236221864456192",
                    "scope-id": 79236221864456192,
                    "persona": "CAMPUS_AP",
                    "resource": "wlan-ssids/Corp-WiFi",
                }
            ]
        },
    )


def test_build_underlay_ssid_url_encodes_spaces():
    """SSID with spaces must use %20 in the URL path."""
    client = MagicMock()
    client.post.return_value = {}

    build_underlay_ssid(client, "Vanity Group", ["500"], "99999")

    create_call = client.post.call_args_list[0]
    assert create_call[0][0] == "/network-config/v1/wlan-ssids/Vanity%20Group"


def test_build_underlay_ssid_body_preserves_spaces():
    """Body fields must keep the original spaces (not %20)."""
    client = MagicMock()
    client.post.return_value = {}

    build_underlay_ssid(client, "Vanity Group", ["500"], "99999")

    body = client.post.call_args_list[0][1]["data"]
    assert body["ssid"] == "Vanity Group"
    assert body["essid"]["name"] == "Vanity Group"


# ---------------------------------------------------------------------------
# build_underlay_ssid — duplicate / already-exists handling
# ---------------------------------------------------------------------------


def _make_http_exc(status: int, text: str) -> Exception:
    exc = Exception("HTTP error")
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    exc.response = resp
    return exc


def test_build_underlay_ssid_create_duplicate_continues_to_scope_map():
    client = MagicMock()
    client.post.side_effect = [
        _make_http_exc(409, "duplicate entry"),  # create returns duplicate
        {"errorCode": "SUCC_001"},               # scope-map succeeds
    ]

    result = build_underlay_ssid(client, "TestSSID", ["1000"], "99999")

    assert result["created"] is True   # treated as success
    assert result["scope_mapped"] is True
    assert result["errors"] == []


def test_build_underlay_ssid_scope_map_already_exists_is_ok():
    client = MagicMock()
    client.post.side_effect = [
        {"errorCode": "SUCC_001"},
        _make_http_exc(409, "scope-map already exists"),
    ]

    result = build_underlay_ssid(client, "TestSSID", ["1000"], "99999")

    assert result["scope_mapped"] is True
    assert result["errors"] == []


def test_build_underlay_ssid_create_hard_failure_aborts():
    client = MagicMock()
    client.post.side_effect = _make_http_exc(500, "internal server error")

    result = build_underlay_ssid(client, "TestSSID", ["1000"], "99999")

    assert result["created"] is False
    assert result["scope_mapped"] is False
    assert len(result["errors"]) == 1
    assert "create_ssid" in result["errors"][0]
    # scope-map must NOT be attempted after a hard create failure
    assert client.post.call_count == 1


def test_build_underlay_ssid_scope_map_hard_failure_recorded():
    client = MagicMock()
    client.post.side_effect = [
        {"errorCode": "SUCC_001"},
        _make_http_exc(400, "bad request"),
    ]

    result = build_underlay_ssid(client, "TestSSID", ["1000"], "99999")

    assert result["created"] is True
    assert result["scope_mapped"] is False
    assert any("scope_map" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# delete_underlay_ssid
# ---------------------------------------------------------------------------


def test_delete_underlay_ssid_dry_run():
    client = MagicMock()
    result = delete_underlay_ssid(client, "Corp-WiFi", dry_run=True)
    client.delete.assert_not_called()
    assert result["deleted"] is True


def test_delete_underlay_ssid_success():
    client = MagicMock()
    client.delete.return_value = {}
    result = delete_underlay_ssid(client, "Corp-WiFi")
    client.delete.assert_called_once_with("/network-config/v1/wlan-ssids/Corp-WiFi")
    assert result["deleted"] is True
    assert result["errors"] == []


def test_delete_underlay_ssid_url_encodes_spaces():
    client = MagicMock()
    client.delete.return_value = {}
    delete_underlay_ssid(client, "My SSID")
    client.delete.assert_called_once_with("/network-config/v1/wlan-ssids/My%20SSID")


def test_delete_underlay_ssid_failure():
    client = MagicMock()
    client.delete.side_effect = Exception("not found")
    result = delete_underlay_ssid(client, "Corp-WiFi")
    assert result["deleted"] is False
    assert len(result["errors"]) == 1


# ---------------------------------------------------------------------------
# get_underlay_ssid
# ---------------------------------------------------------------------------


def test_get_underlay_ssid_found():
    client = MagicMock()
    client.get.return_value = {"ssid": "Corp-WiFi"}
    result = get_underlay_ssid(client, "Corp-WiFi")
    assert result == {"ssid": "Corp-WiFi"}


def test_get_underlay_ssid_not_found_returns_none():
    client = MagicMock()
    exc = Exception("not found")
    resp = MagicMock()
    resp.status_code = 404
    exc.response = resp
    client.get.side_effect = exc
    assert get_underlay_ssid(client, "Missing") is None


# ---------------------------------------------------------------------------
# list_underlay_ssids
# ---------------------------------------------------------------------------


def test_list_underlay_ssids_returns_items():
    client = MagicMock()
    client.get.return_value = {"wlan-ssids": [{"ssid": "A"}, {"ssid": "B"}]}
    items = list_underlay_ssids(client)
    assert len(items) == 2


def test_list_underlay_ssids_empty_on_error():
    client = MagicMock()
    client.get.side_effect = Exception("connection error")
    assert list_underlay_ssids(client) == []
