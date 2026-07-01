from __future__ import annotations

import json
import sqlite3
import sys
import tarfile

from scripts import package_indexes


def _write_index_inputs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    (data_dir / "docs.lance").mkdir(parents=True)
    (data_dir / "docs.lance" / "part.bin").write_text("docs")
    (data_dir / "tools.lance").mkdir()
    (data_dir / "tools.lance" / "part.bin").write_text("tools")
    with sqlite3.connect(data_dir / "specs.sqlite") as conn:
        conn.execute("CREATE TABLE endpoints (id TEXT)")
        conn.execute("CREATE TABLE schemas (id TEXT)")
        conn.execute("CREATE TABLE fields (id TEXT)")

    source_manifest = tmp_path / "source_manifest.json"
    source_manifest.write_text(json.dumps([{"source": "docs"}]) + "\n")
    monkeypatch.setattr(package_indexes, "DATA_DIR", data_dir)
    monkeypatch.setattr(package_indexes, "SOURCE_MANIFEST", source_manifest)


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


def test_package_indexes_embeds_source_manifest(tmp_path, monkeypatch):
    _write_index_inputs(tmp_path, monkeypatch)

    output_dir = tmp_path / "dist"
    archive, _ = package_indexes.package_indexes("vtest", output_dir)
    latest_archive, latest_checksum = package_indexes.write_latest_alias(archive, output_dir)

    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        assert "data/SOURCE-MANIFEST.json" in names
        assert "data/INDEX-MANIFEST.json" in names
        source_data = json.load(tar.extractfile("data/SOURCE-MANIFEST.json"))
        index_data = json.load(tar.extractfile("data/INDEX-MANIFEST.json"))

    assert source_data == [{"source": "docs"}]
    assert index_data["source_manifest"]["sources"] == ["docs"]
    assert latest_archive.name == "centralmcp-rag-index-latest.tar.gz"
    assert latest_archive.read_bytes() == archive.read_bytes()
    assert latest_checksum.read_text().endswith("  centralmcp-rag-index-latest.tar.gz\n")


def test_main_can_skip_latest_alias(tmp_path, monkeypatch):
    _write_index_inputs(tmp_path, monkeypatch)
    output_dir = tmp_path / "dist"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package_indexes.py",
            "--version",
            "vtest",
            "--output-dir",
            str(output_dir),
            "--skip-latest-copy",
        ],
    )

    assert package_indexes.main() == 0
    assert (output_dir / "centralmcp-rag-index-vtest.tar.gz").exists()
    assert (output_dir / "centralmcp-rag-index-vtest.tar.gz.sha256").exists()
    assert not (output_dir / "centralmcp-rag-index-latest.tar.gz").exists()
    assert not (output_dir / "centralmcp-rag-index-latest.tar.gz.sha256").exists()
