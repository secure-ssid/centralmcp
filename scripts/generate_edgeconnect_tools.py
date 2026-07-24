#!/usr/bin/env python3
"""Rebuild the pinned EdgeConnect generated-tool manifest."""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections import Counter
from pathlib import Path

from mcp_servers.openapi_gen.manifest import (
    build_manifest,
    dumps,
    override_path,
    sha256_bytes,
    write_manifest,
)

REPOSITORY = "nowireless4u/hpe-networking-mcp"
COMMIT = "c73e14f6d06fa8e47797553adacff20c91fe2184"
SOURCE_PATH = "vendor/edgeconnect/EdgeConnect-9-7-REST-API.json"
SOURCE_SHA256 = "8f7d90cbd7777e3fac0dc2458249174068f4c373b400d1224f3c3dcc77e34c46"
BLOB_SHA = "f4bc3d2df48f64db3c05f90972a5241526c4876f"


def _source_bytes(path: Path | None) -> bytes:
    if path is not None:
        return path.read_bytes()
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{SOURCE_PATH}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "centralmcp-openapi-generation"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _overrides() -> dict[str, str]:
    doc = json.loads(override_path("edgeconnect").read_text())
    overrides = {str(key): str(value) for key, value in doc["capabilities"].items()}
    overrides.update({str(key): "diagnostic" for key in doc.get("diagnostics", [])})
    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    payload = _source_bytes(args.source)
    digest = sha256_bytes(payload)
    if digest != SOURCE_SHA256:
        raise SystemExit(
            f"EdgeConnect source digest mismatch: expected {SOURCE_SHA256}, received {digest}"
        )
    manifest = build_manifest(
        json.loads(payload),
        platform="edgeconnect",
        source_file=f"{REPOSITORY}@{COMMIT}:{SOURCE_PATH}",
        source_sha256=digest,
        overrides=_overrides(),
    )
    manifest["source"].update(
        {
            "repository": f"https://github.com/{REPOSITORY}",
            "commit": COMMIT,
            "blob_sha": BLOB_SHA,
            "artifact_name": Path(SOURCE_PATH).name,
            "provenance": (
                "Derived from the generated artifact committed by the MIT-licensed "
                "upstream project; the proprietary raw API document is intentionally "
                "not redistributed here."
            ),
        }
    )
    manifest["reviewed_capability_counts"] = dict(
        sorted(Counter(op["capability"] for op in manifest["operations"]).items())
    )

    rendered = dumps(manifest)
    output = Path(__file__).resolve().parents[1] / "mcp_servers/openapi_gen/manifests/edgeconnect.json"
    if args.check:
        if not output.exists() or output.read_text() != rendered:
            raise SystemExit(f"{output} is stale; regenerate it")
        print(f"{output} is current")
        return 0
    write_manifest("edgeconnect", manifest)
    print(
        f"Wrote {output}: {manifest['source']['operation_count']} operations, "
        f"sha256 {digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
