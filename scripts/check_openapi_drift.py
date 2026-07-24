#!/usr/bin/env python3
"""CI-friendly drift check for the Aruba developer-portal OpenAPI registry manifest.

Re-fetches each source reference page recorded in
``ingestion/openapi_registry_manifest.json``, re-resolves its
``oasPublicUrl`` pointer, and compares the freshly-fetched spec's sha256
against the manifest's recorded hash. Designed to run on a schedule (e.g.
a nightly GitHub Actions job) so a developer-portal change -- a spec
update, or another platform migration like the July 2026 ReadMe SuperHub
move -- surfaces as a failed CI job instead of silently stale ingestion
output.

Exit codes:
    0  no drift, no fetch failures.
    1  at least one registry changed (spec content differs from manifest)
       or a source page could not be fetched/parsed.
    2  no manifest found to check (run the ingestion scripts first).

Does not write anything -- this is a read-only check. Run the relevant
ingestion script (``ingestion/scrape_openapi.py`` /
``ingestion/scrape_cnac_spec.py``) to actually refresh a drifted entry.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ingestion.readme_registry import check_entry_drift, load_manifest  # noqa: E402

DEFAULT_MANIFEST_PATH = _REPO_ROOT / "ingestion" / "openapi_registry_manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    registries = manifest.get("registries", {})
    if not registries:
        print(
            f"No registries recorded in {args.manifest} -- run "
            "ingestion/scrape_openapi.py (and scrape_cnac_spec.py) at least "
            "once before checking drift.",
            file=sys.stderr,
        )
        return 2

    print(f"Checking {len(registries)} registries recorded in {args.manifest} for drift...")
    changed = []
    failed = []
    for entry in registries.values():
        result = check_entry_drift(entry)
        if result.status == "unchanged":
            print(f"  OK       {result.registry_id}: {result.detail}")
        elif result.status == "changed":
            changed.append(result)
            print(f"  CHANGED  {result.registry_id}: {result.detail}")
        else:
            failed.append(result)
            print(f"  FAILED   {result.registry_id}: {result.detail}")

    print(
        f"\n{len(registries) - len(changed) - len(failed)} unchanged, "
        f"{len(changed)} changed, {len(failed)} fetch failures."
    )
    if changed or failed:
        print(
            "\nDrift detected. Refresh with ingestion/scrape_openapi.py and/or "
            "ingestion/scrape_cnac_spec.py, then re-run this check."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
