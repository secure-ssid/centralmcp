#!/usr/bin/env python3
"""Package local RAG/OpenAPI indexes for a GitHub Release asset."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
REQUIRED_ARTIFACTS = ("docs.lance", "tools.lance", "specs.sqlite")
SOURCE_MANIFEST = ROOT / "ingestion" / "source_manifest.json"
LATEST_ARCHIVE = "centralmcp-rag-index-latest.tar.gz"


def _project_version() -> str:
    pyproject = ROOT / "pyproject.toml"
    for line in pyproject.read_text().splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    counts: dict[str, int] = {}
    with sqlite3.connect(path) as conn:
        for table in ("endpoints", "schemas", "fields"):
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue
    return counts


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _source_manifest_summary(path: Path | None = None) -> dict[str, object]:
    path = path or SOURCE_MANIFEST
    if not path.exists():
        raise SystemExit(f"Missing source manifest: {_display_path(path)}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid source manifest JSON: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit("Invalid source manifest: top-level JSON value must be a list")
    sources = sorted(
        str(item.get("source", "")).strip()
        for item in data
        if isinstance(item, dict) and str(item.get("source", "")).strip()
    )
    return {
        "path": _display_path(path),
        "sha256": _sha256(path),
        "source_count": len(sources),
        "sources": sources,
    }


def _artifact_manifest(version: str) -> dict[str, object]:
    artifacts: dict[str, object] = {}
    for name in REQUIRED_ARTIFACTS:
        path = DATA_DIR / name
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file()]
            artifacts[name] = {
                "kind": "directory",
                "files": len(files),
                "bytes": sum(item.stat().st_size for item in files),
            }
        elif path.is_file():
            detail: dict[str, object] = {
                "kind": "file",
                "bytes": path.stat().st_size,
            }
            if name == "specs.sqlite":
                detail["counts"] = _sqlite_counts(path)
            artifacts[name] = detail
    return {
        "package_version": version,
        "project_version": _project_version(),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifacts": artifacts,
        "source_manifest": _source_manifest_summary(),
        "restore": "tar -xzf centralmcp-rag-index-<version>.tar.gz",
        "rebuild": (
            "uv run python ingestion/ingest_docs.py && "
            "uv run python scripts/ingest_tools.py --products all"
        ),
    }


def package_indexes(version: str, output_dir: Path) -> tuple[Path, Path]:
    missing = [name for name in REQUIRED_ARTIFACTS if not (DATA_DIR / name).exists()]
    if missing:
        raise SystemExit(f"Missing index artifacts under data/: {', '.join(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"centralmcp-rag-index-{version}.tar.gz"
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    manifest = _artifact_manifest(version)

    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "INDEX-MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(archive, "w:gz") as tar:
            for name in REQUIRED_ARTIFACTS:
                tar.add(DATA_DIR / name, arcname=f"data/{name}")
            tar.add(SOURCE_MANIFEST, arcname="data/SOURCE-MANIFEST.json")
            tar.add(manifest_path, arcname="data/INDEX-MANIFEST.json")

    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n")
    return archive, checksum


def write_latest_alias(archive: Path, output_dir: Path) -> tuple[Path, Path]:
    latest_archive = output_dir / LATEST_ARCHIVE
    latest_checksum = latest_archive.with_suffix(latest_archive.suffix + ".sha256")

    if archive.resolve(strict=False) != latest_archive.resolve(strict=False):
        shutil.copyfile(archive, latest_archive)
    latest_checksum.write_text(f"{_sha256(latest_archive)}  {latest_archive.name}\n")
    return latest_archive, latest_checksum


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=f"v{_project_version()}",
        help="Release/index version label used in the archive filename",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DIST_DIR,
        help="Directory for generated archive and checksum",
    )
    parser.add_argument(
        "--skip-latest-copy",
        action="store_true",
        help="Only write the versioned archive/checksum, not the downloader-friendly latest copy.",
    )
    args = parser.parse_args()

    archive, checksum = package_indexes(args.version, args.output_dir)
    print(f"Wrote {archive}")
    print(f"Wrote {checksum}")
    if not args.skip_latest_copy:
        latest_archive, latest_checksum = write_latest_alias(archive, args.output_dir)
        print(f"Wrote {latest_archive}")
        print(f"Wrote {latest_checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
