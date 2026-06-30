#!/usr/bin/env python3
"""RAG evaluation runner for centralmcp.

Measures whether retrieval selects the *correct* info, so a backend/retrieval
change (e.g. Redis -> LanceDB, adding hybrid+rerank, nomic prefixes) can be
proven instead of asserted. See docs/architecture/RAG-ARCHITECTURE.md.

Usage:
    uv run python tests/eval/run_eval.py                 # default top_k=5
    uv run python tests/eval/run_eval.py --k 8 --verbose
    uv run python tests/eval/run_eval.py --json out.json # machine-readable
    uv run python tests/eval/run_eval.py --ci            # enforce quality thresholds

Metrics:
    source_hit@k  - an expected source substring appears in a top-k file_path/source
    keyword_hit   - an expected keyword appears in returned text (case-insensitive)
    mrr           - reciprocal rank of the first source hit (0 if none)
    api_exact     - for api-lookup rows, keyword_hit treated as the exact-answer signal
                    (ideally served by a future lookup_api tool; falls back to search_docs)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: `uv run --with pyyaml python tests/eval/run_eval.py`")


def _resolve(modattr):
    """Return a plain callable for a (possibly FastMCP-wrapped) tool, or None."""
    mod_name, attr = modattr
    try:
        import importlib
        mod = importlib.import_module(mod_name)
    except Exception:
        return None
    obj = getattr(mod, attr, None)
    if obj is None:
        return None
    # FastMCP may wrap the function; the raw callable is usually at .fn
    return getattr(obj, "fn", obj)


def load_questions() -> list[dict]:
    data = yaml.safe_load((Path(__file__).parent / "rag_eval.yaml").read_text())
    return data["questions"]


def run(k: int, verbose: bool) -> dict:
    search_docs = _resolve(("mcp_servers.rag", "search_docs"))
    lookup_api = _resolve(("mcp_servers.rag", "lookup_api"))  # may not exist yet
    if search_docs is None:
        sys.exit("Could not import mcp_servers.rag.search_docs — is the backend reachable?")

    questions = load_questions()
    rows = []
    for q in questions:
        # Prefer exact structured lookup for api-lookup once lookup_api exists.
        results = None
        if q["type"] == "api-lookup" and lookup_api is not None:
            try:
                results = lookup_api(q["query"])
            except Exception:
                results = None
            # Empty or error-only -> specs hold no confident answer; fall back
            # to prose search (mirrors how an agent uses the two tools).
            if not results or all("error" in h for h in results if isinstance(h, dict)):
                results = None
        if results is None:
            try:
                results = search_docs(q["query"], top_k=k)
            except Exception as e:
                rows.append({**_blank(q), "error": str(e)})
                continue

        hits = results if isinstance(results, list) else [results]
        # source_hit + mrr
        src_rank = 0
        for i, h in enumerate(hits[:k], start=1):
            blob = f"{h.get('source','')} {h.get('file_path','')}".lower()
            if any(s.lower() in blob for s in q.get("expect_sources", [])):
                src_rank = i
                break
        # keyword_hit across all returned text
        text_all = " ".join(str(h.get("text", "")) for h in hits).lower()
        kw_hit = any(kw.lower() in text_all for kw in q.get("expect_keywords", []))

        rows.append({
            "id": q["id"], "type": q["type"],
            "source_hit": src_rank > 0, "rank": src_rank,
            "keyword_hit": kw_hit,
            "mrr": (1.0 / src_rank) if src_rank else 0.0,
        })
        if verbose:
            print(f"  {q['id']:<28} src_hit={rows[-1]['source_hit']!s:<5} "
                  f"rank={src_rank} kw={kw_hit}")

    return _aggregate(rows)


def _blank(q):
    return {"id": q["id"], "type": q["type"], "source_hit": False,
            "rank": 0, "keyword_hit": False, "mrr": 0.0}


def _aggregate(rows: list[dict]) -> dict:
    def frac(pred, subset=None):
        rs = [r for r in rows if (subset is None or r["type"] == subset)]
        return (sum(1 for r in rs if pred(r)) / len(rs)) if rs else 0.0

    summary = {
        "n": len(rows),
        "source_hit@k": round(frac(lambda r: r["source_hit"]), 3),
        "keyword_hit": round(frac(lambda r: r["keyword_hit"]), 3),
        "mrr": round(sum(r["mrr"] for r in rows) / len(rows), 3) if rows else 0.0,
        "howto_recall@k": round(frac(lambda r: r["source_hit"], "howto"), 3),
        "api_exact": round(frac(lambda r: r["keyword_hit"], "api-lookup"), 3),
        "rows": rows,
    }
    return summary


_DEFAULT_THRESHOLDS = {
    "source_hit@k": 0.85,
    "mrr": 0.85,
    "howto_recall@k": 0.85,
    "api_exact": 0.95,
}


def _threshold_failures(summary: dict, thresholds: dict[str, float]) -> list[str]:
    failures = []
    for metric, minimum in thresholds.items():
        actual = float(summary.get(metric, 0.0))
        if actual < minimum:
            failures.append(f"{metric}={actual:.3f} < {minimum:.3f}")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", help="write full results to this path")
    ap.add_argument("--ci", action="store_true", help="enforce default eval thresholds")
    ap.add_argument("--min-source-hit", type=float, default=None)
    ap.add_argument("--min-mrr", type=float, default=None)
    ap.add_argument("--min-howto-recall", type=float, default=None)
    ap.add_argument("--min-api-exact", type=float, default=None)
    args = ap.parse_args()

    print(f"Running RAG eval (top_k={args.k})...")
    summary = run(args.k, args.verbose)
    print("\n=== RAG eval summary ===")
    for key in ("n", "source_hit@k", "keyword_hit", "mrr", "howto_recall@k", "api_exact"):
        print(f"  {key:<16} {summary[key]}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2))
        print(f"\nWrote {args.json}")

    # Non-zero exit if nothing retrieved at all (sanity gate for CI).
    if summary["source_hit@k"] == 0 and summary["keyword_hit"] == 0:
        sys.exit("FAIL: zero retrieval signal — backend likely unreachable or empty index.")

    thresholds: dict[str, float] = {}
    if args.ci:
        thresholds.update(_DEFAULT_THRESHOLDS)
    explicit = {
        "source_hit@k": args.min_source_hit,
        "mrr": args.min_mrr,
        "howto_recall@k": args.min_howto_recall,
        "api_exact": args.min_api_exact,
    }
    thresholds.update({metric: value for metric, value in explicit.items() if value is not None})
    if thresholds:
        failures = _threshold_failures(summary, thresholds)
        if failures:
            sys.exit("FAIL: eval thresholds not met: " + "; ".join(failures))


if __name__ == "__main__":
    main()
