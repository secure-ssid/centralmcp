"""MCP server — Aruba/HPE documentation RAG tools (1 tool).

Covers: semantic search over ingested Aruba Central developer docs,
tech docs, NAC docs, VSG docs, and HTML tech docs via Redis Stack + Ollama.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import READ_ONLY
from pipeline.clients.ollama_client import OllamaClient
from pipeline.clients.redis_client import DOCS_INDEX, get_client as _get_redis_client
from pipeline.clients.redis_client import vector_search

mcp = FastMCP("aruba-rag")

_ollama = OllamaClient()

try:
    _redis = _get_redis_client()
    _redis.ping()
except Exception:
    _redis = None

# Higher boost = preferred when scores are close.
# openapi_specs: ground truth for field schemas and valid enum values.
# developer_docs: official API reference and how-to guides (current product).
# vsg_docs: design guides — conceptually stable, best-practice focused.
# nac_docs: dedicated NAC portal docs.
# tech_docs / techdocs_html: product UI docs — useful but may lag API changes.
_SOURCE_BOOST: dict[str, float] = {
    "openapi_specs": 0.08,
    "developer_docs": 0.05,
    "vsg_docs": 0.03,
    "nac_docs": 0.02,
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


@mcp.tool(annotations=READ_ONLY)
def search_docs(
    query: str,
    top_k: int = 5,
    source: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search Aruba/HPE network documentation.

    Semantic search over developer guides, tech docs, NAC/VSG guides, and OpenAPI specs.
    Call this before searching the web for any Aruba Central config, API, or feature question.

    Args:
        query:    Natural language question or keywords.
        top_k:    Results to return (default 5, max 20).
        source:   Filter by source folder — developer_docs, tech_docs, nac_docs,
                  vsg_docs, techdocs_html, or openapi_specs.
        doc_type: DEPRECATED — use source instead.
    """
    if _redis is None:
        return [{"error": "Redis not available — is the Redis Stack server running?"}]

    top_k = min(top_k, 20)
    query_vector = _ollama.embed(query)

    # Map legacy doc_type to source name when source is not provided
    source_filter = source
    if not source_filter and doc_type:
        source_filter = _DOC_TYPE_TO_SOURCE.get(doc_type)

    # Fetch more candidates so re-ranking has room to promote higher-priority sources
    candidates = vector_search(
        _redis, query_vector, top_k=top_k * 3, source_filter=source_filter
    )

    # Re-rank: boosted_score = raw_score + source_boost. Applies even under filters —
    # a filter narrows the candidate set, boosting still orders within it.
    def boosted(r):
        boost = _SOURCE_BOOST.get(r.get("source", ""), 0.0)
        return r["score"] + boost

    candidates.sort(key=boosted, reverse=True)

    return [
        {
            "text": r["text"][:600] + "…" if len(r["text"]) > 600 else r["text"],
            "source": r["source"],
            "file_path": r["file_path"],
            "score": round(boosted(r), 4),
        }
        for r in candidates[:top_k]
    ]


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
