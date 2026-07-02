"""Regression test: find_tool must return top_k results when enough semantic
hits are available and no keyword hits are found.

Previously the semantic loop's break condition recomputed the "unused
keyword budget" against ``len(by_name)``, which grows as semantic hits are
added — so the threshold shrank every iteration and the loop stopped early
(~top_k/2 results instead of top_k).
"""

from __future__ import annotations

from types import SimpleNamespace

import mcp_servers.tool_router as router


def _hit(i, score=0.9):
    return {
        "name": f"tool_{i}",
        "server": "aruba-rag",
        "description": f"tool {i}",
        "schema_json": "{}",
        "score": score,
    }


def test_find_tool_fills_top_k_with_semantic_hits_when_no_keyword_hits(monkeypatch):
    def load_tools():
        for i in range(10):
            router._tool_index[f"tool_{i}"] = SimpleNamespace(
                annotations=SimpleNamespace(
                    readOnlyHint=True, destructiveHint=False, idempotentHint=True
                )
            )

    monkeypatch.setattr(router, "_BACKEND", "lancedb")
    monkeypatch.setattr(router, "_BACKENDS", {"aruba-rag": "mcp_servers.rag"})
    monkeypatch.setattr(router, "_tool_index", {})
    monkeypatch.setattr(router, "_keyword_hits", lambda query, limit, include_schema=False: [])
    monkeypatch.setattr(router, "_load_all_backends", load_tools)
    monkeypatch.setattr(router._embedder, "embed_query", lambda query: [0.0])
    monkeypatch.setattr(router._lance, "connect", lambda: object())
    monkeypatch.setattr(
        router._lance,
        "search_tools",
        lambda db, query, vec, top_k: [_hit(i) for i in range(10)],
    )

    results = router.find_tool("what is connected right now", top_k=5)

    assert len(results) == 5
