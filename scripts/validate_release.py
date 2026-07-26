#!/usr/bin/env python3
"""Local release validation for centralmcp.

Runs the same practical gates used before pushing: unit tests, optional
RAG/API eval when local indexes exist, a non-mutating tool catalog count,
and an exact tool-identity freshness check when the local LanceDB tool
table exists (catches renames/swaps a count-only comparison would miss --
e.g. one tool removed and a different one added leaves the count
unchanged but the index still stale).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MIN_TOOLS = 204
_BOUNDED_PREVIEW_LIMIT = 10


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return parsed


def _rag_indexes_available(root: Path = ROOT) -> bool:
    return (root / "data/docs.lance").is_dir() and (root / "data/specs.sqlite").is_file()


def _run(command: list[str], label: str) -> None:
    print(f"\n==> {label}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def _registered_tool_identities(products: str | None) -> set[str]:
    """Return exact ``"server:tool_name"`` identities for the registered catalog.

    Used both for the catalog-count floor and for the tool-index freshness
    check: comparing identities (not just a count) catches the case where
    one tool is renamed/swapped for another of the same total count -- a
    count-only comparison would call that index "fresh" when it no longer
    matches the registered catalog at all.
    """
    if products and products.strip().lower() == "all":
        env = os.environ.copy()
        env["CENTRALMCP_PRODUCT_ACCESS"] = "read-write"
        env["CENTRALMCP_GLP_GENERATED_TOOLS"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json\n"
                    "from scripts import ingest_tools\n"
                    "pairs = ingest_tools._collect('all')\n"
                    "ids = sorted(f'{server}:{tool[\"name\"]}' for server, tool in pairs)\n"
                    "print('__CENTRALMCP_TOOL_IDS__=' + json.dumps(ids))\n"
                ),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        marker = "__CENTRALMCP_TOOL_IDS__="
        for line in reversed(completed.stdout.splitlines()):
            if line.startswith(marker):
                return set(json.loads(line.removeprefix(marker)))
        raise RuntimeError("Isolated tool catalog identities did not return an id marker")

    sys.path.insert(0, str(ROOT))
    from scripts import ingest_tools

    previous_access = os.environ.get("CENTRALMCP_PRODUCT_ACCESS")
    os.environ["CENTRALMCP_PRODUCT_ACCESS"] = "read-write"
    try:
        pairs = ingest_tools._collect(products)
    finally:
        if previous_access is None:
            os.environ.pop("CENTRALMCP_PRODUCT_ACCESS", None)
        else:
            os.environ["CENTRALMCP_PRODUCT_ACCESS"] = previous_access
    return {f"{server}:{tool['name']}" for server, tool in pairs}


def _tool_catalog_count(products: str | None) -> int:
    return len(_registered_tool_identities(products))


def _indexed_tool_identities(root: Path = ROOT) -> set[str] | None:
    """Return ``"server:tool_name"`` identities currently in the LanceDB tools table.

    Returns ``None`` when the table hasn't been built yet, matching the old
    ``_tool_index_count``'s "missing index" signal.
    """
    sys.path.insert(0, str(root))
    from pipeline.clients import lance_client

    db = lance_client.connect(root / "data")
    table = lance_client.tools_table(db)
    if table is None:
        return None
    rows = (
        table.search()
        .select(["server", "name"])
        .limit(table.count_rows())
        .to_arrow()
        .to_pylist()
    )
    return {f"{row['server']}:{row['name']}" for row in rows}


def _bounded_preview(identities: list[str]) -> str:
    preview = identities[:_BOUNDED_PREVIEW_LIMIT]
    suffix = "" if len(identities) <= _BOUNDED_PREVIEW_LIMIT else ", ..."
    return ", ".join(preview) + suffix


def _validate_tool_count(total: int, minimum: int) -> None:
    if total < minimum:
        raise SystemExit(f"Tool catalog count {total} is below required minimum {minimum}")


def _validate_tool_index_fresh(indexed: set[str], registered: set[str]) -> None:
    """Fail unless the indexed tool identities exactly match the registered set.

    A plain count comparison (``len(indexed) >= len(registered)``) misses
    the case where a tool was renamed or swapped for a different one: the
    count stays the same, but the index no longer reflects the current
    catalog. Comparing the exact identity sets catches that.
    """
    missing = sorted(registered - indexed)
    stale = sorted(indexed - registered)
    if not missing and not stale:
        return

    details = []
    if missing:
        details.append(
            f"{len(missing)} registered tool(s) missing from the index: "
            f"{_bounded_preview(missing)}"
        )
    if stale:
        details.append(
            f"{len(stale)} indexed tool(s) no longer registered: "
            f"{_bounded_preview(stale)}"
        )
    raise SystemExit(
        "Tool index is stale: " + "; ".join(details) + ". "
        "Rebuild with `CENTRALMCP_PRODUCT_ACCESS=read-write "
        "uv run python scripts/ingest_tools.py --products all`."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true", help="do not run unit tests")
    parser.add_argument("--skip-rag", action="store_true", help="do not run the RAG/API eval gate")
    parser.add_argument(
        "--strict-rag",
        action="store_true",
        help="fail if RAG indexes are missing instead of skipping the eval gate",
    )
    parser.add_argument(
        "--catalog-products",
        default="all",
        help="optional products to include in the non-mutating catalog count",
    )
    parser.add_argument(
        "--min-tools",
        type=_positive_int,
        default=_DEFAULT_MIN_TOOLS,
        help=f"minimum acceptable tool catalog count (default: {_DEFAULT_MIN_TOOLS})",
    )
    parser.add_argument(
        "--strict-tool-index",
        action="store_true",
        help="fail if the local LanceDB tools index is missing",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if not args.skip_tests:
        _run([sys.executable, "-m", "pytest", "tests/unit", "-q"], "Unit tests")

    if not args.skip_rag:
        if _rag_indexes_available():
            _run(
                [sys.executable, "tests/eval/run_eval.py", "--ci"],
                "RAG/API eval gate",
            )
        elif args.strict_rag:
            raise SystemExit("RAG indexes missing: expected data/docs.lance and data/specs.sqlite")
        else:
            print("\n==> RAG/API eval gate", flush=True)
            print("Skipping: data/docs.lance or data/specs.sqlite is missing.", flush=True)

    _run(
        [sys.executable, "scripts/check_generated_tool_manifests.py"],
        "Generated tool manifests",
    )
    _run(
        [sys.executable, "scripts/report_capability_gaps.py", "--check"],
        "Capability gap report",
    )

    print("\n==> Tool catalog count", flush=True)
    registered_ids = _registered_tool_identities(args.catalog_products)
    total = len(registered_ids)
    print(f"{total} tools discovered with products={args.catalog_products!r}")
    _validate_tool_count(total, args.min_tools)
    print(f"Tool catalog floor satisfied: {total} >= {args.min_tools}")

    indexed_ids = _indexed_tool_identities()
    if indexed_ids is None:
        if args.strict_tool_index:
            raise SystemExit("Tool index missing: expected a LanceDB tools table under data/")
        print("Tool index freshness skipped: local LanceDB tools table is missing.")
    else:
        print(f"Tool index contains {len(indexed_ids)} tools")
        _validate_tool_index_fresh(indexed_ids, registered_ids)
        print(
            f"Tool index freshness satisfied: {len(indexed_ids)} indexed tools "
            "exactly match the registered catalog"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
