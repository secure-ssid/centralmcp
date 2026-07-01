from __future__ import annotations

import shutil
import sys
import tarfile

import pytest

from scripts import download_indexes


def test_parse_checksum_accepts_sha256_file_with_filename():
    checksum = download_indexes._parse_checksum(
        "dcfca1d7c9cd3957d047cef0092c5500a6dc9ed885667f8ea9e4b5fcecce32c9  "
        "dist/centralmcp-rag-index-latest.tar.gz\n"
    )

    assert checksum == "dcfca1d7c9cd3957d047cef0092c5500a6dc9ed885667f8ea9e4b5fcecce32c9"


def test_verify_checksum_rejects_mismatch(tmp_path):
    archive = tmp_path / "index.tar.gz"
    archive.write_text("not really a tar")
    checksum = tmp_path / "index.tar.gz.sha256"
    checksum.write_text("0" * 64 + "  index.tar.gz\n")

    with pytest.raises(SystemExit, match="Checksum mismatch"):
        download_indexes._verify_checksum(archive, checksum)


def test_main_downloads_checksum_next_to_archive_by_default(tmp_path, monkeypatch):
    source_data = tmp_path / "source" / "data"
    source_data.mkdir(parents=True)
    (source_data / "INDEX-MANIFEST.json").write_text("{}\n")
    source_archive = tmp_path / "source-index.tar.gz"
    with tarfile.open(source_archive, "w:gz") as tar:
        tar.add(source_data, arcname="data")
    source_checksum = source_archive.with_suffix(source_archive.suffix + ".sha256")
    source_checksum.write_text(
        f"{download_indexes._sha256(source_archive)}  dist/centralmcp-rag-index-latest.tar.gz\n"
    )

    def fake_urlretrieve(url: str, destination):
        source = source_checksum if url.endswith(".sha256") else source_archive
        shutil.copyfile(source, destination)

    archive = tmp_path / "dist" / "centralmcp-rag-index-latest.tar.gz"
    output_dir = tmp_path / "restore"
    monkeypatch.setattr(download_indexes.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "download_indexes.py",
            "--url",
            "https://example.invalid/centralmcp-rag-index-latest.tar.gz",
            "--archive",
            str(archive),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert download_indexes.main() == 0

    assert archive.exists()
    assert archive.with_suffix(archive.suffix + ".sha256").exists()
    assert (output_dir / "data" / "INDEX-MANIFEST.json").exists()
