from __future__ import annotations

import pytest

from scripts import check_security_lifecycle_drift as drift


def test_check_sources_reports_official_source_counts(monkeypatch):
    monkeypatch.setattr(
        drift.sources,
        "fetch_text",
        lambda url: "<xml />" if url == drift.sources.HPE_LIFECYCLE_XML else "csv",
    )
    monkeypatch.setattr(
        drift.sources,
        "parse_changes_csv",
        lambda text: [object()] * drift.MIN_ARUBA_ADVISORIES,
    )
    monkeypatch.setattr(
        drift.sources,
        "parse_hpe_lifecycle_xml",
        lambda text: [object()] * drift.MIN_HPE_LIFECYCLE_NOTICES,
    )
    monkeypatch.setattr(
        drift.sources,
        "render_juniper_lifecycle_page",
        lambda text, url: "rendered",
    )
    monkeypatch.setattr(
        drift.sources,
        "discover_juniper_security_urls",
        lambda: ["https://example.test/bulletin"],
    )

    counts = drift.check_sources()

    assert counts["aruba_advisories"] == drift.MIN_ARUBA_ADVISORIES
    assert counts["hpe_lifecycle_notices"] == drift.MIN_HPE_LIFECYCLE_NOTICES
    assert counts["juniper_lifecycle_pages"] == 3
    assert counts["juniper_security_bulletins"] == 1


def test_check_sources_fails_when_coverage_drops(monkeypatch):
    monkeypatch.setattr(drift.sources, "fetch_text", lambda url: "")
    monkeypatch.setattr(drift.sources, "parse_changes_csv", lambda text: [])
    monkeypatch.setattr(drift.sources, "parse_hpe_lifecycle_xml", lambda text: [])
    monkeypatch.setattr(
        drift.sources,
        "render_juniper_lifecycle_page",
        lambda text, url: "rendered",
    )
    monkeypatch.setattr(
        drift.sources,
        "discover_juniper_security_urls",
        lambda: [],
    )

    with pytest.raises(SystemExit, match="coverage regressed"):
        drift.check_sources()
