#!/usr/bin/env python3
"""Download and unpack the latest prebuilt centralmcp RAG/OpenAPI indexes."""

from __future__ import annotations

import argparse
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = (
    "https://github.com/secure-ssid/centralmcp/releases/latest/download/"
    "centralmcp-rag-index-latest.tar.gz"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Release asset URL")
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

    print(f"Unpacking {args.archive} into {args.output_dir}")
    with tarfile.open(args.archive, "r:gz") as tar:
        tar.extractall(args.output_dir, filter="data")
    print("Indexes restored under data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
