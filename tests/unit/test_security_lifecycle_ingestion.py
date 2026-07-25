"""Security-advisory and lifecycle source parsing tests."""

from __future__ import annotations

import json

import pytest

from ingestion import scrape_security_lifecycle as sl


def test_parse_changes_csv_rejects_path_traversal():
    with pytest.raises(sl.SourceFetchError, match="unsafe advisory path"):
        sl.parse_changes_csv("../secret.json,2026-01-01T00:00:00Z\n")


def test_parse_changes_csv_dedupes_case_insensitive_archive_entries():
    records = sl.parse_changes_csv(
        "2025/ADVISORY.json,2026-01-01T00:00:00Z\n"
        "2025/advisory.json,2026-02-01T00:00:00Z\n"
    )

    assert records == [
        (
            sl.PurePosixPath("2025/advisory.json"),
            "2026-02-01T00:00:00Z",
        )
    ]


def test_render_csaf_advisory_keeps_security_search_fields():
    spec = {
        "document": {
            "title": "AOS security update",
            "aggregate_severity": {"text": "High"},
            "tracking": {
                "id": "HPESBNW00001",
                "initial_release_date": "2026-01-01T00:00:00Z",
                "current_release_date": "2026-01-02T00:00:00Z",
                "status": "final",
                "version": "2",
            },
            "notes": [
                {
                    "title": "Affected Products",
                    "category": "general",
                    "text": "AOS-10 10.7.1 and below.",
                }
            ],
            "references": [
                {"summary": "Original Advisory", "url": "https://example.test/a"}
            ],
        },
        "product_tree": {"branches": [{"name": "AOS-10"}]},
        "vulnerabilities": [
            {
                "cve": "CVE-2026-0001",
                "scores": [
                    {
                        "cvss_v3": {
                            "baseScore": 8.1,
                            "baseSeverity": "HIGH",
                            "vectorString": "CVSS:3.1/AV:N/AC:L",
                        }
                    }
                ],
                "remediations": [
                    {"category": "vendor_fix", "details": "Upgrade to 10.7.2."}
                ],
            }
        ],
    }

    rendered = sl.render_csaf_advisory(spec, "https://example.test/advisory.json")

    assert "HPESBNW00001" in rendered
    assert "CVE-2026-0001" in rendered
    assert "AOS-10 10.7.1 and below" in rendered
    assert "Upgrade to 10.7.2" in rendered
    assert "8.1" in rendered


def test_parse_hpe_lifecycle_xml_groups_sku_rows():
    xml = """\
<Items>
  <Item><ID>1</ID><Type>2</Type><Name>AP family</Name>
    <PubDate>July 1, 2026</PubDate><Desc>End of sale notice.</Desc>
    <LinkA>ap-eos.pdf</LinkA></Item>
  <Item><TableA>AP-1</TableA><TableB>Old AP</TableB>
    <TableC>AP-2</TableC><TableD>New AP</TableD></Item>
  <Item><ID>2</ID><Type>5</Type><Name>ClearPass release</Name></Item>
</Items>
"""

    notices = sl.parse_hpe_lifecycle_xml(xml)

    assert len(notices) == 2
    assert notices[0]["rows"] == [
        {
            "Product SKU": "AP-1",
            "Product Description": "Old AP",
            "Replacement Product SKU": "AP-2",
            "Replacement Product Description": "New AP",
        }
    ]
    rendered = sl.render_hpe_lifecycle_notice(notices[0])
    assert "Product category: Wireless" in rendered
    assert "AP-1" in rendered
    assert "ap-eos.pdf" in rendered


def test_render_juniper_lifecycle_extracts_embedded_tables():
    page = """\
<html><head><title>Mist Dates</title></head><body>
<script>
{"description":'<p>Official lifecycle milestones.</p>',
 "htmlContent":'<table><tr><th>Product</th><th>End of Support</th></tr>
 <tr><td>AP41</td><td>2030-01-01</td></tr></table>'}
</script></body></html>
"""

    rendered = sl.render_juniper_lifecycle_page(page, "https://example.test/mist")

    assert "# Mist Dates" in rendered
    assert "Official lifecycle milestones" in rendered
    assert "AP41" in rendered
    assert "2030-01-01" in rendered


def test_parse_juniper_security_sitemap_filters_to_mist_and_apstra_bulletins():
    sitemap = """\
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://supportportal.juniper.net/s/article/2026-Security-Bulletin-Apstra-CVE-1</loc></url>
  <url><loc>https://supportportal.juniper.net/s/article/2020-Security-Bulletin-Mist-CVE-2</loc></url>
  <url><loc>https://supportportal.juniper.net/s/article/2026-Security-Bulletin-Junos-CVE-3</loc></url>
  <url><loc>https://supportportal.juniper.net/s/article/Mist-troubleshooting</loc></url>
</urlset>
"""

    urls = sl.parse_juniper_security_sitemap(sitemap)

    assert urls == {
        "https://supportportal.juniper.net/s/article/2026-Security-Bulletin-Apstra-CVE-1",
        "https://supportportal.juniper.net/s/article/2020-Security-Bulletin-Mist-CVE-2",
    }


def test_render_juniper_security_requires_article_body():
    rendered = sl.render_juniper_security_advisory(
        "Apstra advisory",
        "Product Affected\nApstra\nSeverity\nHigh\nCVE-2026-0001",
        "https://example.test/advisory",
    )
    assert "CVE-2026-0001" in rendered

    with pytest.raises(sl.SourceFetchError, match="did not contain"):
        sl.render_juniper_security_advisory(
            "Shell only", "Log in", "https://example.test/shell"
        )


def test_sync_security_skips_unchanged_cached_advisory(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "SECURITY_DIR", tmp_path)
    (tmp_path / "2026").mkdir()
    (tmp_path / "2026" / "a.md").write_text("cached")
    (tmp_path / "_manifest.json").write_text(
        json.dumps({"2026/a.json": "2026-01-01T00:00:00Z"})
    )

    monkeypatch.setattr(
        sl,
        "fetch_text",
        lambda url, **kwargs: "2026/a.json,2026-01-01T00:00:00Z\n",
    )

    fetched, skipped = sl.sync_aruba_security()

    assert (fetched, skipped) == (0, 1)
