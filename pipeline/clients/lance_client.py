"""LanceDB embedded document store — hybrid (vector + BM25) search, no server.

Replaces the Redis Stack backend for the default download-and-run path: the
whole index lives in data/ (docs.lance + tools.lance), ships prebuilt via
GitHub Release, and rebuilds with `python ingestion/ingest_docs.py`.

Hybrid search (R5): vector similarity + native BM25 FTS, fused with Reciprocal
Rank Fusion — this catches exact identifiers (WPA3_SAE, endpoint paths) that
pure vector search misses, replacing the static _SOURCE_BOOST re-rank.
Result rows match redis_client.vector_search's shape so rag.py can treat the
backends interchangeably: {text, source, doc_type, file_path, chunk_index, score}.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DOCS_TABLE = "docs"
TOOLS_TABLE = "tools"
EMBEDDING_DIMS = 768

_SOURCE_RE = re.compile(r"^[a-z0-9_]+$")


def connect(data_dir: Path = DATA_DIR):
    """Open (or create) the embedded LanceDB database directory."""
    import lancedb
    data_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(data_dir)


def docs_table(db, table_name: str = DOCS_TABLE):
    """Return the docs table, or None if it hasn't been built yet."""
    try:
        return db.open_table(table_name)
    except Exception:
        return None


def create_docs_table(db, rows: list[dict[str, Any]], table_name: str = DOCS_TABLE):
    """Create the docs table fresh from rows that already carry a 'vector' key."""
    return db.create_table(table_name, data=rows, mode="overwrite")


def build_fts_index(table) -> None:
    """Build the native BM25 FTS index over text (call once after all adds)."""
    table.create_fts_index("text", use_tantivy=False, replace=True)


def hybrid_search(
    db,
    query_text: str,
    query_vector: list[float],
    top_k: int = 15,
    source_filter: str | None = None,
    table_name: str = DOCS_TABLE,
) -> list[dict[str, Any]]:
    """Hybrid (vector + BM25, RRF-fused) search over the docs table.

    Returns rows shaped like redis_client.vector_search; score is the fused
    relevance score (higher = better).
    """
    table = docs_table(db, table_name)
    if table is None:
        raise FileNotFoundError(
            f"LanceDB docs table missing under {DATA_DIR} — build it with "
            "`python ingestion/ingest_docs.py` or download the prebuilt "
            "index from the GitHub Release."
        )
    # limit() truncates EACH leg (vector, FTS) before RRF fusion — fetch deep
    # so fusion sees real overlap, then slice to top_k after.
    q = (
        table.search(query_type="hybrid")
        .vector(query_vector)
        .text(query_text)
        .limit(max(top_k * 3, 15))
    )
    if source_filter:
        if not _SOURCE_RE.match(source_filter):
            raise ValueError(f"invalid source filter: {source_filter!r}")
        q = q.where(f"source = '{source_filter}'", prefilter=True)
    hits = []
    for r in q.to_list()[:top_k]:
        hits.append({
            "text": r.get("text", ""),
            "source": r.get("source", ""),
            "doc_type": r.get("doc_type", ""),
            "file_path": r.get("file_path", ""),
            "chunk_index": int(r.get("chunk_index", 0) or 0),
            "score": round(float(r.get("_relevance_score", 0.0)), 4),
        })
    return hits


def doc_count(db, table_name: str = DOCS_TABLE) -> int:
    table = docs_table(db, table_name)
    return table.count_rows() if table is not None else 0


def source_counts(db, table_name: str = DOCS_TABLE) -> dict[str, int]:
    """Chunks per source — the post-ingest 'every source has >0 docs' assert (R2)."""
    table = docs_table(db, table_name)
    if table is None:
        return {}
    tbl = (
        table.search()
        .select(["source"])
        .limit(table.count_rows())
        .to_arrow()
    )
    counts: dict[str, int] = {}
    for s in tbl.column("source").to_pylist():
        counts[s] = counts.get(s, 0) + 1
    return counts


# ── Tools index (backs tool_router.find_tool) ────────────────────────────────


def create_tools_table(db, rows: list[dict[str, Any]], table_name: str = TOOLS_TABLE):
    """Create the tools table fresh. Rows: server/name/description/schema_json/vector.

    Each row must also carry a 'fts_text' column (name + description combined)
    — the FTS half of hybrid tool search runs over it.
    """
    table = db.create_table(table_name, data=rows, mode="overwrite")
    table.create_fts_index("fts_text", use_tantivy=False, replace=True)
    return table


def tools_table(db, table_name: str = TOOLS_TABLE):
    """Return the tools table, or None if it hasn't been built yet."""
    try:
        return db.open_table(table_name)
    except Exception:
        return None


def tool_count(db, table_name: str = TOOLS_TABLE) -> int | None:
    """Return indexed tool count, or None when the tools table is missing."""
    table = tools_table(db, table_name)
    return table.count_rows() if table is not None else None


def search_tools(
    db,
    query_text: str,
    query_vector: list[float],
    top_k: int = 10,
    table_name: str = TOOLS_TABLE,
) -> list[dict[str, Any]]:
    """Hybrid search over tool definitions (same shape as redis search_tools)."""
    table = tools_table(db, table_name)
    if table is None:
        return []
    q = (
        table.search(query_type="hybrid")
        .vector(query_vector)
        .text(query_text)
        .limit(max(top_k * 3, 15))  # deep fetch per leg, fuse, then slice
    )
    hits = []
    for r in q.to_list()[:top_k]:
        hits.append({
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "server": r.get("server", ""),
            "schema_json": r.get("schema_json", "{}"),
            "score": round(float(r.get("_relevance_score", 0.0)), 4),
        })
    return hits
