from __future__ import annotations

from mcp_servers import rag


def test_ask_docs_uses_lookup_api_for_api_question(monkeypatch):
    monkeypatch.setattr(
        rag,
        "lookup_api",
        lambda question, top_k=3: [
            {
                "text": "POST /network-config/v1alpha1/wlan-ssids creates a WLAN.",
                "source": "openapi_specs",
                "file_path": "openapi_specs/wlan.json#/paths",
                "score": 1.0,
            }
        ],
    )
    monkeypatch.setattr(rag, "search_docs", lambda *args, **kwargs: [])

    out = rag.ask_docs("Which endpoint creates a WLAN?", top_k=3)

    assert out["mode"] == "lookup_api"
    assert "wlan-ssids" in out["answer"]
    assert out["citations"][0]["source"] == "openapi_specs"


def test_ask_docs_falls_back_to_search_docs(monkeypatch):
    monkeypatch.setattr(rag, "lookup_api", lambda question, top_k=3: [])
    monkeypatch.setattr(
        rag,
        "search_docs",
        lambda question, top_k=3, source=None: [
            {
                "text": "Use scope maps to target configuration to sites or groups.",
                "source": "developer_docs",
                "file_path": "developer_docs/scopes.md",
                "score": 0.92,
            }
        ],
    )

    out = rag.ask_docs("How should I target config to a site?", top_k=3)

    assert out["mode"] == "search_docs"
    assert "scope maps" in out["answer"]
    assert out["citations"][0]["file_path"] == "developer_docs/scopes.md"


def test_ask_docs_returns_error_without_citations(monkeypatch):
    monkeypatch.setattr(
        rag,
        "search_docs",
        lambda question, top_k=3, source=None: [{"error": "index missing"}],
    )

    out = rag.ask_docs("How do I configure WLANs?", top_k=3)

    assert out == {"answer": "index missing", "citations": [], "mode": "search_docs"}
