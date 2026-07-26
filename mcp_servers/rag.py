"""MCP server — Aruba/HPE documentation RAG tools (9 tools).

Covers: hybrid (vector + BM25) search over ingested Aruba Central developer
docs, tech docs, NAC docs, VSG docs, and HTML tech docs; exact API
endpoint/schema/enum lookup via the SQLite specs index; exact structured
security-advisory/lifecycle lookup, bounded list/filter/pagination, an
exact-only advisory<->lifecycle correlation, and bounded RAG index
diagnostics (ingestion delta, source freshness, citation completeness).

Default backend is the embedded stack — LanceDB + fastembed, no servers
needed (`clone -> uv sync -> run`). Set CENTRALMCP_RAG_BACKEND=redis for the
optional Redis Stack + Ollama server deployment (vector-only + source boost).
"""

import os
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import READ_ONLY, READ_ONLY_LOCAL
from pipeline import artifact_contracts as contracts
from pipeline.clients import advisory_index, specs_index
from pipeline.clients import rag_diagnostics as rag_diagnostics_client

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
_API_QUERY_HINTS = {
    "api",
    "endpoint",
    "enum",
    "field",
    "schema",
    "method",
    "url",
    "path",
    "payload",
    "request",
    "response",
}


def _clamp_top_k(value: int, max_value: int) -> int:
    return max(1, min(value, max_value))


def _shape(rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    return [
        {
            "text": r["text"][:600] + "…" if len(r["text"]) > 600 else r["text"],
            "source": r["source"],
            "doc_type": r.get("doc_type"),
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


@mcp.tool(annotations=READ_ONLY_LOCAL)
def search_docs(
    query: str,
    top_k: int = 5,
    source: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """Search Aruba/HPE network documentation.

    Hybrid (vector + keyword) search over developer guides, tech docs, and
    NAC/VSG guides. OpenAPI records stay in the exact SQLite `lookup_api`
    index rather than being embedded.

    Args:
        query:    Natural language question or keywords.
        top_k:    Results to return (default 5, range 1-20).
        source:   Filter by source folder — developer_docs, tech_docs, nac_docs,
                  vsg_docs, techdocs_html, aos_techdocs, security_advisories,
                  lifecycle_notices, juniper_lifecycle, or
                  juniper_security_advisories.
        doc_type: DEPRECATED — use source instead.
    """
    top_k = _clamp_top_k(top_k, 20)

    # Map legacy doc_type to source name when source is not provided
    source_filter = source
    if not source_filter and doc_type:
        source_filter = _DOC_TYPE_TO_SOURCE.get(doc_type)

    if _BACKEND == "redis":
        return _search_redis(query, top_k, source_filter)
    return _search_lancedb(query, top_k, source_filter)


@mcp.tool(annotations=READ_ONLY_LOCAL)
def lookup_api(query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Exact Aruba/Mist API lookup — endpoints, schemas, fields, enum values.

    Authoritative, lossless answers from the parsed OpenAPI specs (SQLite, no
    server needed). Use this INSTEAD of search_docs for questions like "what
    enum values does field X accept", "which endpoint configures Y and with
    what method", or "what fields does schema Z have". Returns [] when the
    specs hold no confident answer — fall back to search_docs in that case.

    Args:
        query: Natural language question or keywords (e.g. "auth-type enum
               values for an auth profile", "firmware compliance endpoint").
        top_k: Results to return (default 10, range 1-20).
    """
    try:
        return specs_index.lookup(query, top_k=_clamp_top_k(top_k, 20))
    except FileNotFoundError as exc:
        return [{"error": str(exc)}]


@mcp.tool(annotations=READ_ONLY)
def lookup_advisory(
    product: str | None = None,
    cve: str | None = None,
    advisory_id: str | None = None,
    min_severity: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Look up exact security-advisory metadata from official indexed sources.

    Filter by product text, CVE, or advisory ID. Unlike semantic search, this
    returns structured severity, status, release dates, affected product names,
    CVEs, and authoritative source paths. At least one identifier is required.

    Args:
        product: Product/model/version text contained in the advisory.
        cve: Exact CVE identifier, such as CVE-2025-13914.
        advisory_id: Exact vendor advisory ID, such as HPESBNW04987.
        min_severity: Optional low, medium, high, or critical threshold.
        limit: Results to return (default 20, range 1-200).
    """
    try:
        return advisory_index.lookup_advisories(
            product=product,
            cve=cve,
            advisory_id=advisory_id,
            min_severity=min_severity,
            limit=max(1, min(limit, 200)),
        )
    except FileNotFoundError as exc:
        return [{"error": str(exc)}]


@mcp.tool(annotations=READ_ONLY)
def check_product_lifecycle(
    product: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Find official end-of-sale/end-of-life records for a product or SKU.

    Searches the structured HPE Networking and Juniper Mist/Apstra lifecycle
    tables and returns notice IDs, dates, product/replacement SKUs, source
    family, file path, and authoritative source URL.

    Coverage boundary: the HPE Networking notices are a historical archive
    (legacy HP/H3C/3Com/ProCurve categories; nothing published after 2020)
    plus a static Aruba hardware End of Sale PDF snapshot -- there is no
    reliable official machine-readable source yet for current, post-2020
    Aruba-branded lifecycle notices (see docs/source-lifecycle-coverage.md).
    Do not imply this table is exhaustive for current Aruba products; treat
    an empty result as "not found in these sources", not "still supported".
    """
    try:
        return advisory_index.lookup_lifecycle(
            product,
            limit=max(1, min(limit, 200)),
        )
    except FileNotFoundError as exc:
        return [{"error": str(exc)}]


@mcp.tool(annotations=READ_ONLY)
def list_advisories(
    product: str | None = None,
    cve: str | None = None,
    advisory_id: str | None = None,
    min_severity: str | None = None,
    source_family: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List/paginate security advisories with exact filters — no identifier required.

    Companion to `lookup_advisory` (which requires an identifier): this
    lists and paginates across every advisory matching zero or more exact
    filters, and reports how many rows matched in total so a caller knows
    whether to page further.

    Args:
        product: Product/model/version text contained in the advisory.
        cve: Exact CVE identifier, such as CVE-2025-13914.
        advisory_id: Exact vendor advisory ID, such as HPESBNW04987.
        min_severity: Optional low, medium, high, or critical threshold.
        source_family: Exact source authority — security_advisories or
            juniper_security_advisories.
        since: Inclusive lower-bound date (YYYY-MM-DD) on the advisory's
            release date.
        until: Inclusive upper-bound date (YYYY-MM-DD) on the advisory's
            release date.
        limit: Rows per page (default 20, range 1-200).
        offset: Rows to skip for pagination (default 0).
    """
    try:
        return advisory_index.list_advisories(
            product=product,
            cve=cve,
            advisory_id=advisory_id,
            min_severity=min_severity,
            source_family=source_family,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def list_lifecycle_events(
    product: str | None = None,
    product_sku: str | None = None,
    replacement_sku: str | None = None,
    category: str | None = None,
    event_type: str | None = None,
    source_family: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List/paginate lifecycle notices with exact filters — no identifier required.

    Companion to `check_product_lifecycle` (which requires a product/SKU
    text match): this lists and paginates across every lifecycle record
    matching zero or more exact filters, with the same coverage boundary
    (see `check_product_lifecycle` and docs/source-lifecycle-coverage.md) —
    an empty result means "not found in these sources", not "still
    supported".

    Args:
        product: Free-text product/model search across the indexed record.
        product_sku: Exact product SKU in the record's parsed `product_skus`
            list (case-insensitive).
        replacement_sku: Exact replacement SKU in the record's parsed
            `replacement_skus` list (case-insensitive).
        category: Exact product category, e.g. Switches or Wireless.
        event_type: Exact lifecycle event type/state, e.g.
            end-of-sale/end-of-life.
        source_family: Exact source authority — lifecycle_notices or
            juniper_lifecycle.
        since: Inclusive lower-bound date (YYYY-MM-DD) on the published date.
        until: Inclusive upper-bound date (YYYY-MM-DD) on the published date.
        limit: Rows per page (default 20, range 1-200).
        offset: Rows to skip for pagination (default 0).
    """
    try:
        return advisory_index.list_lifecycle_events(
            product=product,
            product_sku=product_sku,
            replacement_sku=replacement_sku,
            category=category,
            event_type=event_type,
            source_family=source_family,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def correlate_advisory_lifecycle(
    product: str | None = None,
    advisory_id: str | None = None,
    cve: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Link advisory product applicability to lifecycle records — exact only.

    For each matching advisory, its listed product/model strings are
    normalized (case/whitespace only) and compared against every lifecycle
    record's product/replacement SKUs. A link is reported only on exact
    normalized equality — this is never a fuzzy or semantic claim. An
    advisory product with no such match is reported under
    `unresolved_products`, not silently dropped and not implied "not
    affected" or "still supported".

    Args:
        product: Product/model/version text (at least one of product,
            advisory_id, or cve is required).
        advisory_id: Exact vendor advisory ID, such as HPESBNW04987.
        cve: Exact CVE identifier, such as CVE-2025-13914.
        limit: Advisories to correlate (default 20, range 1-200).
    """
    try:
        return advisory_index.correlate_advisory_lifecycle(
            product=product,
            advisory_id=advisory_id,
            cve=cve,
            limit=limit,
        )
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=READ_ONLY)
def rag_diagnostics(include_ingestion_delta: bool = True) -> dict[str, Any]:
    """Report bounded RAG security/lifecycle index freshness and completeness.

    Combines three read-only, network-free diagnostics scoped to the
    structured advisory/lifecycle sources (not the full prose corpus):

    - `citation_completeness`: per source authority, what fraction of
      records have their key citation fields populated (advisory
      severity/date, lifecycle published date/SKUs, source URL, ID).
    - `source_freshness`: the latest `source_freshness_result` artifact from
      `scripts/check_security_lifecycle_drift.py`, reduced to per-status
      counts (fresh/stale/unavailable/changed/coverage_gap) plus each
      source's bounded entry. Reported as an error (not "fresh") until that
      script has produced an artifact.
    - `ingestion_delta`: new/changed/removed/unchanged content-hash counts
      for the security-advisory and lifecycle source families versus the
      current LanceDB docs table — no embedding, no writes.

    Never returns raw source bodies — only counts, statuses, and short
    bounded strings already stored in the persisted artifacts/tables.

    Args:
        include_ingestion_delta: set False to skip the slightly slower
            content-hash diff and return only freshness + citation counts.
    """
    result: dict[str, Any] = {}
    try:
        result["citation_completeness"] = advisory_index.citation_completeness()
    except FileNotFoundError as exc:
        result["citation_completeness"] = {"error": str(exc)}

    try:
        result["source_freshness"] = rag_diagnostics_client.freshness_summary()
    except (FileNotFoundError, contracts.ArtifactValidationError) as exc:
        result["source_freshness"] = {"error": str(exc)}

    if include_ingestion_delta:
        try:
            result["ingestion_delta"] = rag_diagnostics_client.ingestion_delta()
        except (FileNotFoundError, ValueError) as exc:
            result["ingestion_delta"] = {"error": str(exc)}

    return result


_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
_ADVISORY_ID_RE = re.compile(r"\bHPESBNW\d{4,}\b", re.IGNORECASE)


def _is_api_question(question: str) -> bool:
    tokens = {
        tok.strip(".,:;?!()[]{}\"'").lower()
        for tok in question.replace("/", " ").replace("-", " ").split()
    }
    return bool(tokens & _API_QUERY_HINTS)


def _extract_exact_identifier(question: str) -> dict[str, str] | None:
    """Return an exact lookup_advisory kwarg found in the question, if any.

    Only an unambiguous, literal CVE ID or vendor advisory ID triggers exact
    routing — never a guessed/extracted product name, which would risk a
    wrong (fuzzy) filter presented as authoritative.
    """
    cve_match = _CVE_RE.search(question)
    if cve_match:
        return {"cve": cve_match.group(0).upper()}
    advisory_match = _ADVISORY_ID_RE.search(question)
    if advisory_match:
        return {"advisory_id": advisory_match.group(0).upper()}
    return None


def _summarize_advisory(hit: dict[str, Any]) -> str:
    """Bounded, non-body summary for an ask_docs answer over lookup_advisory hits."""
    parts = [str(hit.get("advisory_id") or hit.get("title") or "advisory")]
    if hit.get("title") and hit.get("advisory_id"):
        parts.append(f"— {hit['title']}")
    if hit.get("severity"):
        parts.append(f"(severity: {hit['severity']})")
    if hit.get("current_release"):
        parts.append(f"current release: {hit['current_release']}")
    text = " ".join(parts)
    return text[:900] + "…" if len(text) > 900 else text


def _citation(hit: dict[str, Any]) -> dict[str, Any]:
    citation = {
        "file_path": hit.get("file_path"),
        "source": hit.get("source") or hit.get("source_family"),
        "doc_type": hit.get("doc_type"),
        "score": hit.get("score"),
    }
    for key in (
        "source_url",
        "advisory_id",
        "severity",
        "status",
        "current_release",
        "notice_id",
        "published",
        "category",
        "event_type",
    ):
        if hit.get(key) is not None:
            citation[key] = hit[key]
    for key in ("cves", "product_skus", "replacement_skus"):
        value = hit.get(key)
        if isinstance(value, list) and value:
            citation[key] = value[:5]
    return citation


@mcp.tool(annotations=READ_ONLY_LOCAL)
def ask_docs(
    question: str,
    top_k: int = 3,
    source: str | None = None,
) -> dict[str, Any]:
    """Return a compact cited answer from local docs/API indexes.

    Token-saving companion to `search_docs`: it returns the shortest useful
    extractive answer plus citations instead of dumping multiple long chunks.
    A question containing a literal CVE ID or vendor advisory ID consults
    `lookup_advisory` first (exact, never a guessed product filter);
    otherwise API-shaped questions consult `lookup_api` first; both fall
    back to prose RAG when no exact match exists.
    """
    k = max(1, min(top_k, 5))
    mode = "search_docs"
    hits: list[dict[str, Any]] = []

    if source is None:
        identifier = _extract_exact_identifier(question)
        if identifier:
            advisory_hits = lookup_advisory(limit=k, **identifier)
            if advisory_hits and "error" not in advisory_hits[0]:
                mode = "lookup_advisory"
                hits = advisory_hits

    if not hits and source is None and _is_api_question(question):
        api_hits = lookup_api(question, top_k=k)
        if api_hits and "error" not in api_hits[0]:
            mode = "lookup_api"
            hits = api_hits

    if not hits:
        hits = search_docs(question, top_k=k, source=source)
        mode = "search_docs"

    if not hits:
        return {
            "answer": "No matching local documentation was found.",
            "citations": [],
            "mode": mode,
        }
    if "error" in hits[0]:
        return {"answer": hits[0]["error"], "citations": [], "mode": mode}

    top = hits[0]
    if mode == "lookup_advisory":
        answer = _summarize_advisory(top)
    else:
        text = str(top.get("text", "")).strip()
        answer = text[:900] + "…" if len(text) > 900 else text
    return {
        "answer": answer,
        "citations": [_citation(hit) for hit in hits[:k]],
        "mode": mode,
    }


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        SecretTokenizeMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(
        mcp,
        [
            NullStripMiddleware(),
            RateLimitMiddleware(rate=8.0),
            SecretTokenizeMiddleware(),
        ],
    )
    from mcp_servers.shared import run_server
    run_server(mcp)
