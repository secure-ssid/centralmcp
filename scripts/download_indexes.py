#!/usr/bin/env python3
"""Download and unpack the latest prebuilt centralmcp RAG/OpenAPI indexes."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = (
    "https://github.com/secure-ssid/centralmcp/releases/latest/download/"
    "centralmcp-rag-index-latest.tar.gz"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksum(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        checksum = stripped.split()[0].lower()
        if len(checksum) == 64 and all(char in "0123456789abcdef" for char in checksum):
            return checksum
        raise ValueError(f"Invalid checksum line: {line!r}")
    raise ValueError("Checksum file is empty")


def _verify_checksum(archive: Path, checksum_file: Path) -> None:
    expected = _parse_checksum(checksum_file.read_text())
    actual = _sha256(archive)
    if actual != expected:
        raise SystemExit(
            f"Checksum mismatch for {archive}: expected {expected}, got {actual}"
        )


def _member_target(member: tarfile.TarInfo, output_dir: Path) -> Path:
    name = PurePosixPath(member.name)
    parts = name.parts
    if name.is_absolute() or not parts or ".." in parts or parts[0] != "data":
        raise SystemExit(f"Unsafe archive member path: {member.name!r}")

    output_root = output_dir.resolve(strict=False)
    target = output_root.joinpath(*parts).resolve(strict=False)
    try:
        target.relative_to(output_root)
    except ValueError as exc:
        raise SystemExit(f"Unsafe archive member path: {member.name!r}") from exc
    return target


def _extract_data_archive(tar: tarfile.TarFile, output_dir: Path) -> None:
    for member in tar:
        target = _member_target(member, output_dir)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit(f"Unsafe archive member type: {member.name!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            raise SystemExit(f"Could not read archive member: {member.name!r}")
        with source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _swap_into_place(staging_data: Path, data_dir: Path) -> None:
    """Move each extracted artifact over its live counterpart via renames.

    Extracting straight into the live data/ interleaved old and new files:
    an interrupted run corrupted the Lance tables, and stale local files not
    present in the archive (e.g. higher-numbered Lance version manifests from
    local ingests) survived the extract — so a "successful" download could
    keep serving the old index. Renaming whole artifacts replaces them
    completely, and the old copy is only deleted after the new one is in
    place.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted(staging_data.iterdir()):
        live = data_dir / entry.name
        old = data_dir / f"{entry.name}.old-tmp"
        _remove(old)
        if live.exists() or live.is_symlink():
            os.rename(live, old)
        os.rename(entry, live)
        _remove(old)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Release asset URL")
    parser.add_argument(
        "--checksum-url",
        default=None,
        help="Checksum URL. Defaults to <url>.sha256 unless --skip-checksum is set.",
    )
    parser.add_argument(
        "--checksum-file",
        type=Path,
        default=None,
        help="Where to store the downloaded checksum file",
    )
    parser.add_argument(
        "--skip-checksum",
        action="store_true",
        help="Download and unpack without verifying a .sha256 checksum",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=ROOT / "dist" / "centralmcp-rag-index-latest.tar.gz",
        help="Where to store the downloaded archive",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT,
        help="Directory where the archive is unpacked",
    )
    args = parser.parse_args()

    args.archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {args.url}")
    urllib.request.urlretrieve(args.url, args.archive)

    if not args.skip_checksum:
        checksum_url = args.checksum_url or f"{args.url}.sha256"
        checksum_file = args.checksum_file or args.archive.with_suffix(
            args.archive.suffix + ".sha256"
        )
        checksum_file.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {checksum_url}")
        urllib.request.urlretrieve(checksum_url, checksum_file)
        print(f"Verifying {args.archive}")
        _verify_checksum(args.archive, checksum_file)

    print(f"Unpacking {args.archive} into {args.output_dir}")
    # Extract into a staging dir on the same filesystem, then swap artifacts
    # into the live data/ via renames — never write into live data/ directly.
    staging_root = args.output_dir / ".index-download-staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    try:
        with tarfile.open(args.archive, "r:gz") as tar:
            _extract_data_archive(tar, staging_root)
        staging_data = staging_root / "data"
        if not staging_data.is_dir():
            raise SystemExit("Archive contained no data/ directory")
        _swap_into_place(staging_data, args.output_dir / "data")
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    print("Indexes restored under data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
