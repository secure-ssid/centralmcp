#!/usr/bin/env python3
"""Local release validation for centralmcp.

Runs the same practical gates used before pushing: unit tests, optional
RAG/API eval when local indexes exist, a non-mutating tool catalog count,
and a stale-tool-index check when the local LanceDB tool table exists.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MIN_TOOLS = 204


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


def _tool_catalog_count(products: str | None) -> int:
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
    return len(pairs)


def _tool_index_count(root: Path = ROOT) -> int | None:
    sys.path.insert(0, str(root))
    from pipeline.clients import lance_client

    db = lance_client.connect(root / "data")
    return lance_client.tool_count(db)


def _validate_tool_count(total: int, minimum: int) -> None:
    if total < minimum:
        raise SystemExit(f"Tool catalog count {total} is below required minimum {minimum}")


def _validate_tool_index_fresh(indexed: int, registered: int) -> None:
    if indexed < registered:
        raise SystemExit(
            f"Tool index is stale: {indexed} indexed tools is below {registered} registered tools. "
            "Rebuild with `uv run python scripts/ingest_tools.py --products all`."
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

    print("\n==> Tool catalog count", flush=True)
    total = _tool_catalog_count(args.catalog_products)
    print(f"{total} tools discovered with products={args.catalog_products!r}")
    _validate_tool_count(total, args.min_tools)
    print(f"Tool catalog floor satisfied: {total} >= {args.min_tools}")

    indexed = _tool_index_count()
    if indexed is None:
        if args.strict_tool_index:
            raise SystemExit("Tool index missing: expected a LanceDB tools table under data/")
        print("Tool index freshness skipped: local LanceDB tools table is missing.")
    else:
        print(f"Tool index contains {indexed} tools")
        _validate_tool_index_fresh(indexed, total)
        print(f"Tool index freshness satisfied: {indexed} >= {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
