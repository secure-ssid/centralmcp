#!/usr/bin/env python3
"""Report whether the official Mist OpenAPI file has advanced past our pin."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ingestion.fetch_mist_openapi import DEFAULT_PATH, DEFAULT_REF, REPOSITORY  # noqa: E402


def main() -> int:
    query = urllib.parse.urlencode({"path": DEFAULT_PATH, "per_page": 1})
    url = f"https://api.github.com/repos/{REPOSITORY}/commits?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "centralmcp-openapi-drift",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            commits = json.load(response)
    except Exception as exc:
        print(f"Failed to query {REPOSITORY}: {exc}", file=sys.stderr)
        return 1

    if not commits:
        print(f"No commits returned for {REPOSITORY}/{DEFAULT_PATH}", file=sys.stderr)
        return 1
    latest = commits[0]["sha"]
    if latest != DEFAULT_REF:
        print(
            f"Mist OpenAPI drift detected: pinned {DEFAULT_REF}, latest {latest}. "
            "Review the new spec, then update fetch_mist_openapi.py."
        )
        return 1
    print(f"Mist OpenAPI pin is current: {REPOSITORY}@{DEFAULT_REF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
