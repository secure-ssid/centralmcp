#!/usr/bin/env python3
"""Report whether pinned nowireless4u/hpe-networking-mcp inputs have advanced.

centralmcp treats the MIT-licensed community project
``nowireless4u/hpe-networking-mcp`` as a set of reviewed *benchmark/input*
pins -- never as API authority -- for three unrelated purposes:

- the vendored GreenLake OpenAPI specs used by
  ``scripts/generate_glp_tools.py`` (``vendor/greenlake``);
- the Axis Atmos Cloud platform source reviewed by
  ``scripts/generate_axis_manifest.py``
  (``src/hpe_networking_mcp/platforms/axis``); and
- the capability-benchmark counts recorded in
  ``docs/capability-benchmark-snapshot.json``, reproduced from
  ``README.md`` and ``scripts/build_spec_index.py`` at the pinned commit.

Each of those pins is a single whole-tree commit SHA covering the entire
reviewed upstream repository, not a per-path commit. That whole-tree SHA is
almost never itself the last commit that touched any one watched path, so it
must never be compared directly against a path-scoped "latest commit" query
-- doing so reports false drift on every run. Instead, for each watched
path this script derives two *path-scoped* commits from GitHub's commits API
(``per_page=1``):

- **baseline** -- the latest commit that touched the path as of the
  reviewed pin (``sha=<pin>&path=<path>``), i.e. what the path looked like
  when the pin was reviewed; and
- **latest** -- the latest commit that touched the path on the upstream
  default branch (``path=<path>``, no ``sha``), i.e. what the path looks
  like now.

The path is CURRENT when ``baseline == latest`` (nothing has touched the
path since the reviewed pin) and DRIFTED when they differ (something has
touched the path after the reviewed pin). This script re-derives the pins
from their existing source-of-truth modules/files (it holds no copy of its
own), never fetches raw source contents, and never writes to any file.

Exit codes:
    0  every watched path is current (baseline == latest).
    1  drift detected, a GitHub fetch failed or returned malformed data,
       the pins are internally inconsistent, or there are no watched
       inputs at all.

Refresh guidance when drift is reported: review the changed path(s)
upstream, then regenerate the affected artifact(s) with
``scripts/generate_glp_tools.py``, ``scripts/generate_axis_manifest.py``,
or ``scripts/report_capability_gaps.py`` (updating
``docs/capability-benchmark-snapshot.json``) as applicable, and only then
advance the reviewed pin.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import generate_axis_manifest as _axis_gen  # noqa: E402
from scripts import generate_glp_tools as _glp_gen  # noqa: E402

GITHUB_API_BASE = "https://api.github.com"
USER_AGENT = "centralmcp-nowireless-source-drift"
DEFAULT_TIMEOUT = 20.0
DEFAULT_BENCHMARK_PATH = _REPO_ROOT / "docs" / "capability-benchmark-snapshot.json"

STATUS_CURRENT = "current"
STATUS_DRIFT = "drift"
STATUS_ERROR = "error"

# Bound how many labels/entries are ever rendered in one line, defense in
# depth against an upstream metadata file ballooning source_evidence.
_MAX_LABELS_SHOWN = 6
_SHA_SHOWN = 12

_REPO_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_OWNER_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class DriftConfigError(Exception):
    """Pinned metadata is missing, malformed, or internally inconsistent."""


class DriftFetchError(Exception):
    """GitHub could not be queried, or returned unusable data."""


def normalize_repo(value: str) -> str:
    """Normalize a repository identifier to ``owner/repo``.

    Accepts either a bare ``owner/repo`` string or a ``github.com`` URL
    (with or without scheme, trailing slash, or ``.git`` suffix).
    """
    text = (value or "").strip()
    match = _REPO_URL_RE.match(text)
    if match:
        owner, repo = match.groups()
        return f"{owner}/{repo}"
    if _OWNER_REPO_RE.match(text):
        return text
    raise DriftConfigError(f"cannot normalize repository identifier: {value!r}")


def load_glp_pin() -> dict[str, str]:
    """Return the GLP vendored-spec pin from scripts/generate_glp_tools.py."""
    return {
        "label": "glp_vendor_specs",
        "repo": normalize_repo(_glp_gen.UPSTREAM_REPO),
        "ref": _glp_gen.UPSTREAM_REF,
        "path": _glp_gen.VENDOR_DIR,
    }


def load_axis_pin() -> dict[str, str]:
    """Return the Axis platform-source pin from scripts/generate_axis_manifest.py."""
    return {
        "label": "axis_platform_source",
        "repo": normalize_repo(_axis_gen.REPOSITORY),
        "ref": _axis_gen.COMMIT,
        "path": _axis_gen.AXIS_ROOT,
    }


def load_capability_benchmark_pins(
    snapshot_path: Path = DEFAULT_BENCHMARK_PATH,
) -> list[dict[str, str]]:
    """Return the capability-benchmark path pins from the committed snapshot JSON."""
    try:
        raw = snapshot_path.read_text()
    except OSError as exc:
        raise DriftConfigError(
            f"cannot read capability benchmark snapshot at {snapshot_path}: {exc}"
        ) from exc
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DriftConfigError(
            f"capability benchmark snapshot is not valid JSON: {snapshot_path}"
        ) from exc

    repo_field = data.get("repository")
    commit = data.get("commit")
    if not repo_field or not commit:
        raise DriftConfigError(
            f"capability benchmark snapshot missing repository/commit: {snapshot_path}"
        )
    normalized_repo = normalize_repo(repo_field)

    repo_url_field = data.get("repository_url")
    if repo_url_field:
        normalized_repo_url = normalize_repo(repo_url_field)
        if normalized_repo_url != normalized_repo:
            raise DriftConfigError(
                "capability benchmark snapshot repository/repository_url mismatch: "
                f"{repo_field!r} vs {repo_url_field!r}"
            )

    paths: set[str] = set()
    for evidence in data.get("source_evidence", []) or []:
        path = evidence.get("path") if isinstance(evidence, dict) else None
        if path:
            paths.add(path)
    reproduction = data.get("indexed_endpoint_reproduction") or {}
    script_path = reproduction.get("source_script") if isinstance(reproduction, dict) else None
    if script_path:
        paths.add(script_path)

    if not paths:
        raise DriftConfigError(
            f"capability benchmark snapshot has no watched source paths: {snapshot_path}"
        )

    return [
        {
            "label": f"capability_benchmark:{path}",
            "repo": normalized_repo,
            "ref": commit,
            "path": path,
        }
        for path in sorted(paths)
    ]


def dedupe_inputs(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Collapse entries sharing the same (repo, ref, path) into one record."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for entry in entries:
        key = (entry["repo"], entry["ref"], entry["path"])
        if key not in merged:
            merged[key] = {
                "repo": entry["repo"],
                "ref": entry["ref"],
                "path": entry["path"],
                "labels": [entry["label"]],
            }
            order.append(key)
        else:
            merged[key]["labels"].append(entry["label"])
    return [merged[key] for key in order]


def collect_watched_inputs(
    *, benchmark_path: Path = DEFAULT_BENCHMARK_PATH
) -> list[dict[str, Any]]:
    """Gather, validate, and deduplicate all watched path-specific pins.

    Raises DriftConfigError if any source module's pin cannot be
    normalized, or if the pinned repositories disagree with each other
    (all three sources are expected to reference the same reviewed
    upstream tree).
    """
    entries: list[dict[str, str]] = [load_glp_pin(), load_axis_pin()]
    entries.extend(load_capability_benchmark_pins(benchmark_path))

    repos = {entry["repo"] for entry in entries}
    if len(repos) > 1:
        detail = ", ".join(f"{entry['label']}={entry['repo']}" for entry in entries)
        raise DriftConfigError(
            f"inconsistent pinned repository across source-of-truth modules: {detail}"
        )

    return dedupe_inputs(entries)


def fetch_path_commit(
    repo: str,
    path: str,
    *,
    sha: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Return the SHA of the most recent commit that touched ``path`` in ``repo``.

    Args:
        repo: normalized ``owner/repo`` identifier.
        path: repo-relative path to scope the commits query to.
        sha: optional starting point (branch, tag, or commit SHA) to scope
            the search to history reachable from that ref. Pass the
            reviewed whole-tree pin to get the *baseline* path commit "as
            of" that review point. Omit (default) to search the upstream
            default branch, giving the *latest* path commit. Never compare
            the whole-tree pin itself against a path-scoped commit --
            always fetch both a baseline and a latest path commit and
            compare those.
        token: optional GitHub token sent as ``Authorization: Bearer`` --
            never logged or included in any returned/raised value.
        timeout: finite socket timeout in seconds.
    """
    params: dict[str, str | int] = {}
    if sha:
        params["sha"] = sha
    params["path"] = path
    params["per_page"] = 1
    query = urllib.parse.urlencode(params)
    url = f"{GITHUB_API_BASE}/repos/{repo}/commits?{query}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise DriftFetchError(f"HTTP {exc.code} fetching {repo}@{path}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise DriftFetchError(f"network error fetching {repo}@{path}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DriftFetchError(f"timed out fetching {repo}@{path}") from exc

    try:
        commits = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DriftFetchError(f"malformed (non-JSON) response for {repo}@{path}") from exc

    if not isinstance(commits, list) or not commits:
        raise DriftFetchError(f"no commits returned for {repo}@{path}")

    latest = commits[0]
    found_sha = latest.get("sha") if isinstance(latest, dict) else None
    if not found_sha or not isinstance(found_sha, str):
        raise DriftFetchError(f"malformed commit entry for {repo}@{path} (missing sha)")
    return found_sha


def evaluate_input(
    entry: dict[str, Any],
    *,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Compare a watched path's baseline (as of the reviewed pin) and latest commits.

    Fetches two path-scoped commits -- never compares the whole-tree pin
    directly against a path commit. ``baseline`` is the latest commit that
    touched the path as of the reviewed pin (``sha=<pin>``); ``latest`` is
    the latest commit that touched the path on the default branch. Status
    is CURRENT when they match, DRIFT when they differ, and ERROR
    (fail-closed) when either fetch fails or returns malformed data.
    """
    result: dict[str, Any] = {
        "repo": entry["repo"],
        "path": entry["path"],
        "pin": entry["ref"],
        "labels": entry["labels"],
        "baseline": None,
        "latest": None,
    }

    try:
        baseline = fetch_path_commit(
            entry["repo"], entry["path"], sha=entry["ref"], token=token, timeout=timeout
        )
    except DriftFetchError as exc:
        result["status"] = STATUS_ERROR
        result["detail"] = f"baseline fetch failed: {exc}"
        return result
    result["baseline"] = baseline

    try:
        latest = fetch_path_commit(entry["repo"], entry["path"], token=token, timeout=timeout)
    except DriftFetchError as exc:
        result["status"] = STATUS_ERROR
        result["detail"] = f"latest fetch failed: {exc}"
        return result
    result["latest"] = latest

    if baseline == latest:
        result["status"] = STATUS_CURRENT
        result["detail"] = "no path changes since reviewed pin"
    else:
        result["status"] = STATUS_DRIFT
        result["detail"] = "path changed upstream since reviewed pin"
    return result


def format_result_line(result: dict[str, Any]) -> str:
    """Render one bounded, compact status line for a watched path."""
    labels = result["labels"][:_MAX_LABELS_SHOWN]
    labels_str = ",".join(labels)
    if len(result["labels"]) > _MAX_LABELS_SHOWN:
        labels_str += ",..."
    status = result["status"].upper()
    pin = result["pin"][:_SHA_SHOWN]
    baseline = result["baseline"][:_SHA_SHOWN] if result["baseline"] else "?"
    latest = result["latest"][:_SHA_SHOWN] if result["latest"] else "?"
    return (
        f"  {status:7s} {result['repo']}@{result['path']} ({labels_str}) "
        f"pin={pin} baseline={baseline} latest={latest}: {result['detail']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-path",
        type=Path,
        default=DEFAULT_BENCHMARK_PATH,
        help="path to docs/capability-benchmark-snapshot.json (for testing)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-request GitHub API timeout in seconds",
    )
    args = parser.parse_args(argv)

    # Never logged: only ever placed into a request header, never printed.
    token = os.environ.get("GITHUB_TOKEN") or None

    try:
        entries = collect_watched_inputs(benchmark_path=args.benchmark_path)
    except DriftConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if not entries:
        print(
            "No watched nowireless4u/hpe-networking-mcp inputs configured.",
            file=sys.stderr,
        )
        return 1

    print(f"Checking {len(entries)} pinned nowireless4u/hpe-networking-mcp path(s) for drift...")
    results = [evaluate_input(entry, token=token, timeout=args.timeout) for entry in entries]
    for result in results:
        print(format_result_line(result))

    current = [r for r in results if r["status"] == STATUS_CURRENT]
    drifted = [r for r in results if r["status"] == STATUS_DRIFT]
    errored = [r for r in results if r["status"] == STATUS_ERROR]

    print(f"\n{len(current)} current, {len(drifted)} drifted, {len(errored)} fetch errors.")

    if errored:
        print(
            "\nSome paths could not be checked: GitHub fetch failed or returned "
            "malformed data (rate limiting, network issues, or a bad/expired "
            "GITHUB_TOKEN are common causes). This is a configuration/fetch "
            "failure, not confirmed upstream drift -- fix access and rerun."
        )
    if drifted:
        print(
            "\nDrift detected in reviewed nowireless4u/hpe-networking-mcp inputs. These "
            "are community benchmark/input pins, not API authority: review the changed "
            "path(s) upstream, then regenerate the affected artifact(s) with "
            "scripts/generate_glp_tools.py, scripts/generate_axis_manifest.py, or "
            "scripts/report_capability_gaps.py (docs/capability-benchmark-snapshot.json) "
            "as applicable before advancing the reviewed pin."
        )

    if drifted or errored:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
