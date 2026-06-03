"""MCP server — Aruba/HPE documentation RAG tools (2 tools).

Covers: hybrid (vector + BM25) search over ingested Aruba Central developer
docs, tech docs, NAC docs, VSG docs, and HTML tech docs; exact API
endpoint/schema/enum lookup via the SQLite specs index.

Default backend is the embedded stack — LanceDB + fastembed, no servers
needed (`clone -> uv sync -> run`). Set CENTRALMCP_RAG_BACKEND=redis for the
optional Redis Stack + Ollama server deployment (vector-only + source boost).
"""

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import READ_ONLY
from pipeline.clients import specs_index

mcp = FastMCP("aruba-rag")

_BACKEND = os.getenv("CENTRALMCP_RAG_BACKEND", "lancedb").strip().lower()

if _BACKEND == "redis":
    from pipeline.clients.ollama_client import OllamaClient
    from pipeline.clients.redis_client import get_client as _get_redis_client
    from pipeline.clients.redis_client import vector_search

    _ollama = OllamaClient()
    try:
        _redis = _get_redis_client()
        _redis.ping()
    except Exception:
        _redis = None
else:
    from pipeline.clients import lance_client
    from pipeline.clients.embed_client import EmbedClient

    _embedder = EmbedClient()  # lazy — the ONNX model loads on first query

# Redis backend only — the LanceDB path replaces this static re-rank with
# hybrid BM25+vector RRF fusion (R5).
# Higher boost = preferred when scores are close.
# openapi_specs: ground truth for field schemas and valid enum values.
# developer_docs: official API reference and how-to guides (current product).
# vsg_docs: design guides — conceptually stable, best-practice focused.
# nac_docs: dedicated NAC portal docs.
# tech_docs / techdocs_html: product UI docs — useful but may lag API changes.
# Recalibrated (doubled) after the H13 cosine fix: similarity now spans the full
# 0–1 range (was effectively halved), so boosts double to keep the same relative gaps.
_SOURCE_BOOST: dict[str, float] = {
    "openapi_specs": 0.16,
    "developer_docs": 0.10,
    "vsg_docs": 0.06,
    "nac_docs": 0.04,
    "tech_docs": 0.0,
    "techdocs_html": 0.0,
}

_DOC_TYPE_TO_SOURCE: dict[str, str] = {
    "developer-docs": "developer_docs",
    "tech-docs": "tech_docs",
    "techdocs-html": "techdocs_html",
    "nac": "nac_docs",
    "vsg": "vsg_docs",
    "openapi": "openapi_specs",
    "aos-techdocs": "aos_techdocs",
}


def _shape(rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    return [
        {
            "text": r["text"][:600] + "…" if len(r["text"]) > 600 else r["text"],
            "source": r["source"],
            "file_path": r["file_path"],
            "score": round(r["score"], 4),
        }
        for r in rows[:top_k]
    ]


def _search_lancedb(query: str, top_k: int, source_filter: str | None) -> list[dict[str, Any]]:
    try:
        db = lance_client.connect()
        query_vector = _embedder.embed_query(query)
        hits = lance_client.hybrid_search(
            db, query, query_vector, top_k=top_k, source_filter=source_filter
        )
    except (FileNotFoundError, ValueError) as exc:
        return [{"error": str(exc)}]
    return _shape(hits, top_k)


def _search_redis(query: str, top_k: int, source_filter: str | None) -> list[dict[str, Any]]:
    if _redis is None:
        return [{"error": "Redis not available — is the Redis Stack server running?"}]

    query_vector = _ollama.embed_query(query)
    # Fetch more candidates so re-ranking has room to promote higher-priority sources
    candidates = vector_search(
        _redis, query_vector, top_k=top_k * 3, source_filter=source_filter
    )

    # Re-rank: boosted_score = raw_score + source_boost. Applies even under filters —
    # a filter narrows the candidate set, boosting still orders within it.
    for r in candidates:
        r["score"] = r["score"] + _SOURCE_BOOST.get(r.get("source", ""), 0.0)
    candidates.sort(key=lambda r: r["score"], reverse=True)
    return _shape(candidates, top_k)


@mcp.tool(annotations=READ_ONLY)
def search_docs(
    query: str,
    top_k: int = 5,
    source: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search Aruba/HPE network documentation.

    Hybrid (vector + keyword) search over developer guides, tech docs, NAC/VSG
    guides, and OpenAPI specs. Call this before searching the web for any Aruba
    Central config, API, or feature question.

    Args:
        query:    Natural language question or keywords.
        top_k:    Results to return (default 5, max 20).
        source:   Filter by source folder — developer_docs, tech_docs, nac_docs,
                  vsg_docs, techdocs_html, aos_techdocs, or openapi_specs.
        doc_type: DEPRECATED — use source instead.
    """
    top_k = min(top_k, 20)

    # Map legacy doc_type to source name when source is not provided
    source_filter = source
    if not source_filter and doc_type:
        source_filter = _DOC_TYPE_TO_SOURCE.get(doc_type)

    if _BACKEND == "redis":
        return _search_redis(query, top_k, source_filter)
    return _search_lancedb(query, top_k, source_filter)


@mcp.tool(annotations=READ_ONLY)
def lookup_api(query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Exact Aruba Central API lookup — endpoints, schemas, fields, enum values.

    Authoritative, lossless answers from the parsed OpenAPI specs (SQLite, no
    server needed). Use this INSTEAD of search_docs for questions like "what
    enum values does field X accept", "which endpoint configures Y and with
    what method", or "what fields does schema Z have". Returns [] when the
    specs hold no confident answer — fall back to search_docs in that case.

    Args:
        query: Natural language question or keywords (e.g. "auth-type enum
               values for an auth profile", "firmware compliance endpoint").
        top_k: Results to return (default 10, max 20).
    """
    try:
        return specs_index.lookup(query, top_k=min(top_k, 20))
    except FileNotFoundError as exc:
        return [{"error": str(exc)}]


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    from mcp_servers.shared import run_server
    run_server(mcp)
