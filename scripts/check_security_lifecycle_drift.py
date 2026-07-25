#!/usr/bin/env python3
"""Check official security/lifecycle sources for parseability and minimum coverage."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion import scrape_security_lifecycle as sources  # noqa: E402

MIN_ARUBA_ADVISORIES = 90
MIN_HPE_LIFECYCLE_NOTICES = 300
MIN_JUNIPER_SECURITY_BULLETINS = 1


def check_sources() -> dict[str, int]:
    advisories = sources.parse_changes_csv(
        sources.fetch_text(sources.ARUBA_CSAF_CHANGES)
    )
    notices = sources.parse_hpe_lifecycle_xml(
        sources.fetch_text(sources.HPE_LIFECYCLE_XML)
    )
    for url in sources.JUNIPER_LIFECYCLE_URLS.values():
        sources.render_juniper_lifecycle_page(sources.fetch_text(url), url)
    bulletins = sources.discover_juniper_security_urls()

    counts = {
        "aruba_advisories": len(advisories),
        "hpe_lifecycle_notices": len(notices),
        "juniper_lifecycle_pages": len(sources.JUNIPER_LIFECYCLE_URLS),
        "juniper_security_bulletins": len(bulletins),
    }
    minimums = {
        "aruba_advisories": MIN_ARUBA_ADVISORIES,
        "hpe_lifecycle_notices": MIN_HPE_LIFECYCLE_NOTICES,
        "juniper_security_bulletins": MIN_JUNIPER_SECURITY_BULLETINS,
    }
    below = [
        f"{name}={counts[name]} below {minimum}"
        for name, minimum in minimums.items()
        if counts[name] < minimum
    ]
    if below:
        raise SystemExit("Security/lifecycle source coverage regressed: " + "; ".join(below))
    return counts


def main() -> int:
    for name, count in check_sources().items():
        print(f"{name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
