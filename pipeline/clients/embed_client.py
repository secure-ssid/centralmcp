"""In-process embeddings via fastembed (ONNX) — no Ollama server required.

Runs nomic-embed-text-v1.5 locally (same model semantics as the Ollama
`nomic-embed-text` deployment it replaces) with the task prefixes nomic
requires (R3): passages get "search_document: ", queries "search_query: ".
Unlike the Ollama path, prefixes are ALWAYS on — this client only ever talks
to the LanceDB corpus, which is ingested with prefixes from day one.

fastembed batches natively (R4), so a full re-ingest is minutes, not a 40k-call
serial loop. The ONNX model (~250 MB) downloads to the HF cache on first use.
"""

from __future__ import annotations

from typing import Iterable

EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIMS = 768
# nomic-embed context window; truncate to stay safe (matches the Ollama client)
_MAX_CHARS = 6000
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "


class EmbedClient:
    """Drop-in replacement for OllamaClient's embed_document/embed_query."""

    def __init__(self, model: str = EMBEDDING_MODEL):
        self.model_name = model
        self._model = None  # lazy — keep MCP server startup fast

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_document(self, texts: Iterable[str], batch_size: int = 64) -> list[list[float]]:
        """Embed passages for indexing, with nomic's search_document prefix."""
        prefixed = [_DOC_PREFIX + t[:_MAX_CHARS] for t in texts]
        return [v.tolist() for v in self.model.embed(prefixed, batch_size=batch_size)]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string, with nomic's search_query prefix."""
        vectors = list(self.model.embed([_QUERY_PREFIX + text[:_MAX_CHARS]]))
        return vectors[0].tolist()
