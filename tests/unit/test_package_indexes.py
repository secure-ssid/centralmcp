from __future__ import annotations

from scripts import package_indexes


def test_source_manifest_summary_tracks_rag_sources():
    summary = package_indexes._source_manifest_summary()

    assert summary["path"] == "ingestion/source_manifest.json"
    assert len(summary["sha256"]) == 64
    assert summary["source_count"] == len(summary["sources"])
    assert "techdocs_html" in summary["sources"]
    assert "feature_navigator" in summary["sources"]


def test_artifact_manifest_includes_source_manifest():
    manifest = package_indexes._artifact_manifest("vtest")

    assert manifest["source_manifest"]["path"] == "ingestion/source_manifest.json"
    assert "openapi_specs" in manifest["source_manifest"]["sources"]
