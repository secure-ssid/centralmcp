#!/usr/bin/env python3
"""Build the derived generated-operation manifests for the optional product
backends: ClearPass (CPPM), ArubaOS 8, UXI, and Apstra.

Provenance / licensing
----------------------
* ClearPass, AOS8, and UXI operation metadata is fetched at build time from the
  Aruba developer portal's ReadMe SuperHub ``api-registry`` (the same public
  registry the docs ingestion pipeline uses). Only the *derived* compact
  operation manifest is written under
  ``mcp_servers/openapi_gen/manifests/<platform>.json`` — the raw proprietary
  OpenAPI documents are never committed.
* Apstra has no authoritative distributable full OpenAPI spec, so its manifest
  is the current maximum *reviewed* operation set derived from the MIT-licensed
  upstream Apstra backend (``mcp_servers/apstra.py`` curated tools). This is
  explicitly NOT full OpenAPI coverage.

Usage::

    uv run python scripts/build_optional_product_manifests.py            # all
    uv run python scripts/build_optional_product_manifests.py --platform uxi
    uv run python scripts/build_optional_product_manifests.py --check     # CI drift

Network access to ``dash.readme.com`` is required for clearpass/uxi/aos8 (not
for apstra). ``--check`` rebuilds in memory and fails if the committed manifest
is stale, without writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ingestion import readme_registry as rr  # noqa: E402
from mcp_servers.openapi_gen import manifest as M  # noqa: E402

PORTAL = "https://developer.arubanetworks.com"

# ClearPass 6.12.x groups its ~335-path surface into 16 ReadMe api-registry
# categories. Discovered by walking the portal reference sidebar and reading
# each page's ``oasPublicUrl`` pointer (see ingestion/readme_registry.py).
CPPM_REGISTRIES: dict[str, str] = {
    "318h42r1xml85aa55": "API Operations",
    "1ajpg01cml85anrq": "Certificate Authority",
    "at4cgf25ml85b3v0": "Endpoint Visibility",
    "at4cgf1qml85bng3": "Enforcement Profile",
    "15btrd1aeml85c5da": "Global Server Configuration",
    "15btrd12ml85csbt": "Guest Actions",
    "at4cgfzml85da6p": "Guest Configuration",
    "1wxkxoml85duds": "Identities",
    "318h42r1cml85e6m3": "Insight",
    "a48hf0fml85elb0": "Integrations",
    "1ajpg031ml85ey7m": "Local Server Configuration",
    "a48hf0uml85faaa": "Logs",
    "a48hf010ml85fo52": "Platform Certificates",
    "a48hf01qml85g8pg": "Policy Elements",
    "at4cgf25ml85gob5": "Session Control",
    "at4cgf38ml85h0ju": "Tools And Utilities",
}


def _fetch(rid: str) -> dict:
    return rr.fetch_registry_spec(rr.OasPointer("x", "v", rid))


def _sha(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def build_clearpass() -> dict:
    docs = []
    prov = []
    for rid, cat in CPPM_REGISTRIES.items():
        spec = _fetch(rid)
        sha = _sha(spec)
        fname = f"cppm-{cat.lower().replace(' ', '-')}-{rid}.json"
        docs.append((fname, sha, spec))
        prov.append(
            {
                "category": cat,
                "registry_id": rid,
                "registry_url": f"{rr.REGISTRY_BASE_URL}/{rid}",
                "portal_project": "aruba-cppm",
                "sha256": sha,
                "openapi": spec.get("openapi"),
                "title": spec.get("info", {}).get("title"),
                "spec_version": spec.get("info", {}).get("version"),
                "path_count": len(spec.get("paths", {})),
            }
        )
    man = M.build_merged_manifest(docs, platform="clearpass", overrides=M.load_overrides("clearpass"))
    man["provenance"] = {
        "acquired_from": "Aruba developer portal ReadMe SuperHub API registries",
        "portal": f"{PORTAL}/cppm/reference",
        "spec_version": "6.12.7",
        "note": "Derived operation metadata only; raw proprietary specs are not committed.",
        "registries": sorted(prov, key=lambda p: p["category"]),
    }
    return man


def build_single(
    platform: str, rid: str, spec_version: str, portal_ref: str, strip_params: list[str] | None = None
) -> dict:
    spec = _fetch(rid)
    sha = _sha(spec)
    man = M.build_manifest(
        spec,
        platform=platform,
        source_file=f"{platform}-{rid}.json",
        source_sha256=sha,
        overrides=M.load_overrides(platform),
    )
    stripped = {p.lower() for p in (strip_params or [])}
    occurrences = 0
    if stripped:
        for op in man["operations"]:
            kept = []
            for prm in op.get("parameters", []):
                if prm.get("name", "").lower() in stripped:
                    occurrences += 1
                    continue
                kept.append(prm)
            op["parameters"] = kept
    man["provenance"] = {
        "acquired_from": "Aruba developer portal ReadMe SuperHub API registry",
        "portal": portal_ref,
        "registry_id": rid,
        "registry_url": f"{rr.REGISTRY_BASE_URL}/{rid}",
        "spec_version": spec_version,
        "spec_title": spec.get("info", {}).get("title"),
        "note": "Derived operation metadata only; raw proprietary specs are not committed.",
    }
    if stripped:
        man["provenance"]["stripped_auth_parameters"] = sorted(strip_params or [])
        man["provenance"]["stripped_auth_parameter_occurrences"] = occurrences
    return man


def build_apstra() -> dict:
    from scripts._apstra_operations import build_apstra_manifest

    return build_apstra_manifest()


_BUILDERS = {
    "clearpass": build_clearpass,
    "uxi": lambda: build_single("uxi", "2j1jmli8l514", "6.7.0", f"{PORTAL}/uxi/reference"),
    "aos8": lambda: build_single(
        "aos8",
        "cjpas1kkx7bible",
        "8.0 (ArubaOS JSON API 1.0)",
        f"{PORTAL}/aos8/reference",
        strip_params=["UIDARUBA"],
    ),
    "apstra": build_apstra,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", choices=sorted(_BUILDERS), help="build one platform (default: all)")
    ap.add_argument("--check", action="store_true", help="verify committed manifests are current; do not write")
    args = ap.parse_args()

    platforms = [args.platform] if args.platform else list(_BUILDERS)
    rc = 0
    for platform in platforms:
        man = _BUILDERS[platform]()
        rendered = M.dumps(man)
        out_path = M.manifest_path(platform)
        count = len(man["operations"])
        if args.check:
            if not out_path.exists() or out_path.read_text() != rendered:
                print(f"DRIFT: {platform} manifest is missing or stale ({count} ops).", file=sys.stderr)
                rc = 1
            else:
                print(f"OK: {platform} manifest current ({count} ops).")
        else:
            M.write_manifest(platform, man)
            print(f"Wrote {out_path.relative_to(_REPO_ROOT)} ({count} operations).")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
