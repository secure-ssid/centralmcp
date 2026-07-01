#!/usr/bin/env python3
"""Download and unpack the latest prebuilt centralmcp RAG/OpenAPI indexes."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
import urllib.request
from pathlib import Path

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
    with tarfile.open(args.archive, "r:gz") as tar:
        tar.extractall(args.output_dir, filter="data")
    print("Indexes restored under data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
