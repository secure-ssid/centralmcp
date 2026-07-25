from __future__ import annotations

import sqlite3

import pytest

from pipeline.clients import advisory_index


@pytest.fixture
def built_index(tmp_path):
    sources = tmp_path / "sources"
    security = sources / "security_advisories"
    lifecycle = sources / "lifecycle_notices"
    juniper = sources / "juniper_security_advisories"
    security.mkdir(parents=True)
    lifecycle.mkdir()
    juniper.mkdir()

    (security / "hpesbnw04987.md").write_text(
        """<!-- source: https://example.test/hpesbnw04987.json -->

# ArubaOS security update

- Advisory ID: HPESBNW04987
- Aggregate severity: Critical
- Initial release: 2025-01-01
- Current release: 2025-02-01
- Status: final

## Product catalog

- ArubaOS 10
- AP-635

## Vulnerabilities

### CVE-2025-12345
"""
    )
    (juniper / "apstra.md").write_text(
        """<!-- source: https://example.test/apstra -->

# Apstra Security Bulletin CVE-2025-13914

Product Affected: Apstra 5.x
Severity: High
"""
    )
    (lifecycle / "123-ap.md").write_text(
        """<!-- source: https://example.test/eos.xml -->

# Aruba AP lifecycle notice

- Notice ID: 123
- Product category: Wireless
- Published: 2024-03-01

## Affected and replacement products

- Product SKU: AP-635; Product Description: Campus AP; Replacement Product SKU: AP-655
"""
    )

    db_path = tmp_path / "specs.sqlite"
    counts = advisory_index.build(sources, db_path)
    return db_path, counts


def test_builds_structured_advisory_and_lifecycle_tables(built_index):
    db_path, counts = built_index

    assert counts == {"advisories": 2, "lifecycle_events": 1, "skipped": 0}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 1


def test_lookup_advisory_by_id_cve_product_and_severity(built_index):
    db_path, _counts = built_index

    by_id = advisory_index.lookup_advisories(
        advisory_id="hpesbnw04987",
        db_path=db_path,
    )
    by_cve = advisory_index.lookup_advisories(
        cve="CVE-2025-12345",
        db_path=db_path,
    )
    by_product = advisory_index.lookup_advisories(
        product="AP-635",
        min_severity="high",
        db_path=db_path,
    )

    assert by_id[0]["severity"] == "Critical"
    assert by_cve[0]["advisory_id"] == "HPESBNW04987"
    assert by_product[0]["products"] == ["ArubaOS 10", "AP-635"]
    assert by_product[0]["cves"] == ["CVE-2025-12345"]


def test_lookup_lifecycle_returns_skus_and_replacement(built_index):
    db_path, _counts = built_index

    rows = advisory_index.lookup_lifecycle("AP-635", db_path=db_path)

    assert rows[0]["notice_id"] == "123"
    assert rows[0]["product_skus"] == ["AP-635"]
    assert rows[0]["replacement_skus"] == ["AP-655"]


def test_lookup_requires_identifiers_and_valid_severity(built_index):
    db_path, _counts = built_index

    with pytest.raises(ValueError, match="provide product"):
        advisory_index.lookup_advisories(db_path=db_path)
    with pytest.raises(ValueError, match="min_severity"):
        advisory_index.lookup_advisories(
            product="Aruba",
            min_severity="urgent",
            db_path=db_path,
        )


def test_missing_structured_tables_has_actionable_error(tmp_path):
    db_path = tmp_path / "specs.sqlite"
    sqlite3.connect(db_path).close()

    with pytest.raises(FileNotFoundError, match="Structured advisory index"):
        advisory_index.lookup_advisories(product="Aruba", db_path=db_path)
