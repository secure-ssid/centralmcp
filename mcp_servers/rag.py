"""MCP server — Aruba/HPE documentation RAG tools (1 tool).

Covers: semantic search over ingested Aruba Central developer docs,
tech docs, NAC docs, VSG docs, and HTML tech docs via Qdrant + Ollama.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from pipeline.clients.ollama_client import OllamaClient
from pipeline.clients.qdrant_client import DOCS_COLLECTION, QDRANT_URL

mcp = FastMCP("aruba-rag")

_ollama = OllamaClient()

try:
    from qdrant_client import QdrantClient as _QdrantClient
    _qdrant = _QdrantClient(url=QDRANT_URL)
except Exception:
    _qdrant = None

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


@mcp.tool()
def search_docs(
    query: str,
    top_k: int = 5,
    source: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search Aruba/HPE network documentation for relevant content.

    Performs semantic search over ingested docs (developer guides, tech docs,
    NAC docs, VSG docs). Use this before searching the web for any question
    about Aruba Central configuration, APIs, or network features.

    Results are re-ranked by source priority so that API schemas (openapi_specs)
    and official developer guides (developer_docs) rank above UI-focused tech docs
    when scores are close.

    Args:
        query:    Natural language question or keywords.
        top_k:    Number of results to return (default 5, max 20).
        source:   Optional folder filter — one of: developer_docs, tech_docs,
                  nac_docs, vsg_docs, techdocs_html, openapi_specs.
        doc_type: DEPRECATED — prefer `source`. Still accepted: developer-docs,
                  tech-docs, techdocs-html, nac, vsg, openapi.

    Returns:
        List of chunks with text, source, doc_type, file_path, score, and
        boosted_score (used for ranking).
    """
    if _qdrant is None:
        return [{"error": "Qdrant not available — is Docker running?"}]

    top_k = min(top_k, 20)
    query_vector = _ollama.embed(query)

    query_filter = None
    conditions = []
    if source or doc_type:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if source:
            conditions.append(
                FieldCondition(key="source", match=MatchValue(value=source))
            )
        if doc_type:
            conditions.append(
                FieldCondition(key="doc_type", match=MatchValue(value=doc_type))
            )
        query_filter = Filter(must=conditions)

    # Fetch more candidates so re-ranking has room to promote higher-priority sources
    candidates = _qdrant.query_points(
        collection_name=DOCS_COLLECTION,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k * 3,
    ).points

    # Re-rank: boosted_score = raw_score + source_boost. Applies even under filters —
    # a filter narrows the candidate set, boosting still orders within it.
    def boosted(r):
        boost = _SOURCE_BOOST.get(r.payload.get("source", ""), 0.0)
        return r.score + boost

    candidates.sort(key=boosted, reverse=True)

    return [
        {
            "text": r.payload.get("text", ""),
            "source": r.payload.get("source"),
            "doc_type": r.payload.get("doc_type"),
            "file_path": r.payload.get("file_path"),
            "chunk_index": r.payload.get("chunk_index"),
            "score": round(r.score, 4),
            "boosted_score": round(boosted(r), 4),
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
    mcp.run()
