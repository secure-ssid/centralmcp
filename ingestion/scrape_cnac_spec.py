#!/usr/bin/env python3
"""Extract the Central NAC Service OpenAPI spec from developer.arubanetworks.com.

The CNAC client-registration API (cnac-mac-reg, cnac-visitor,
cnac-named-mpsk-reg, cnac-dpp-reg, certificates, jobs) is NOT served by the
internal-ui cnxconfig docs host that scrape_openapi.py pulls from — but the
readme.io reference pages embed the full OAS document ("oasDefinition") in
their HTML. This script fetches one such page and extracts the largest
embedded definition.

Usage: python ingestion/scrape_cnac_spec.py
Writes: ingestion/sources/openapi_specs/cnac-client-registration.json
Then rebuild the index: python -m pipeline.clients.specs_index --build
"""
import json
import re
import urllib.request
from pathlib import Path

PAGE_URL = "https://developer.arubanetworks.com/new-central-config/reference/mac-registration"
OUT_PATH = Path(__file__).parent / "sources" / "openapi_specs" / "cnac-client-registration.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def extract_oas(html: str) -> dict:
    """Return the largest oasDefinition object embedded in the page."""
    dec = json.JSONDecoder()
    best: dict = {}
    for m in re.finditer(r'"oasDefinition":', html):
        try:
            obj, _ = dec.raw_decode(html[m.end():])
        except Exception:
            continue
        if len(obj.get("paths", {})) > len(best.get("paths", {})):
            best = obj
    if not best:
        raise SystemExit("No oasDefinition found — page layout may have changed")
    return best


def main() -> int:
    req = urllib.request.Request(PAGE_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    spec = extract_oas(html)
    info = spec.get("info", {})
    print(f"Extracted {info.get('title')!r} v{info.get('version')}: "
          f"{len(spec.get('paths', {}))} paths, "
          f"{len(spec.get('components', {}).get('schemas', {}))} schemas")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(spec, indent=1))
    print(f"Wrote {OUT_PATH}")
    print("Rebuild the index: python -m pipeline.clients.specs_index --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
