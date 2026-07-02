"""RAG tools query only local indexes (LanceDB/SQLite/Ollama) — never the
live Central/GLP API — so openWorldHint should be False, distinguishing them
from every other READ_ONLY tool that does call the live API."""

from __future__ import annotations

from mcp_servers import rag


def test_rag_tools_are_read_only_but_not_open_world():
    for name in ("search_docs", "lookup_api", "ask_docs"):
        annotations = rag.mcp._tool_manager._tools[name].annotations
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.openWorldHint is False
