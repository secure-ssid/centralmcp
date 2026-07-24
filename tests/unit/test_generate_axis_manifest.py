from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_servers.openapi_gen.manifest import dumps
from scripts.generate_axis_manifest import (
    AxisSourceError,
    build_axis_manifest,
    check_manifest,
    parse_registries,
    validate_registry_source,
    verify_source_digests,
)


def test_axis_manifest_generation_is_deterministic_and_current():
    first = build_axis_manifest()
    second = build_axis_manifest()
    committed = Path("mcp_servers/openapi_gen/manifests/axis.json")

    assert dumps(first) == dumps(second)
    assert dumps(first) == committed.read_text()
    assert first["schema_version"] == 2
    assert first["source"]["operation_count"] == 25


def test_axis_source_digest_mismatch_is_rejected():
    sources = {"axis.py": b"reviewed"}
    expected = {"axis.py": "0" * 64}

    with pytest.raises(AxisSourceError, match="digest mismatch"):
        verify_source_digests(sources, expected_digests=expected)


def test_axis_registry_source_changes_are_detected():
    source = b"""
TOOLS = {"status": ["axis_get_new_status"]}
_DISABLED_TOOLS = {
    "custom_ip_categories": [
        "axis_get_custom_ip_categories",
        "axis_manage_custom_ip_category",
    ],
    "ip_feed_categories": [
        "axis_get_ip_feed_categories",
        "axis_manage_ip_feed_category",
    ],
}
"""

    with pytest.raises(AxisSourceError, match="enabled TOOLS registry changed"):
        validate_registry_source(source)


def test_axis_stale_manifest_detection(tmp_path):
    path = tmp_path / "axis.json"
    stale = build_axis_manifest()
    stale["operations"][0]["summary"] = "stale"
    path.write_text(json.dumps(stale))

    with pytest.raises(AxisSourceError, match="is stale"):
        check_manifest(path)


def test_axis_registry_parser_requires_both_registries():
    source = b'TOOLS = {"status": ["axis_get_status"]}\n'

    with pytest.raises(AxisSourceError, match="_DISABLED_TOOLS is missing"):
        parse_registries(source)
