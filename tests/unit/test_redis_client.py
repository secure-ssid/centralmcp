from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pipeline.clients import redis_client


def test_get_client_uses_env_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6380/1")

    with patch("pipeline.clients.redis_client.redis.from_url") as from_url:
        redis_client.get_client()

    from_url.assert_called_once_with("redis://cache.internal:6380/1", decode_responses=False)


def test_get_client_prefers_explicit_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6380/1")

    with patch("pipeline.clients.redis_client.redis.from_url") as from_url:
        redis_client.get_client("redis://override:6379/9")

    from_url.assert_called_once_with("redis://override:6379/9", decode_responses=False)


def _fake_client_returning(score):
    """Build a fake Redis client whose ft().search() returns one doc with `score`."""
    doc = SimpleNamespace(
        text="t", source="developer_docs", doc_type="developer-docs",
        file_path="f.md", chunk_index="0", name="n", description="d",
        server="aruba", schema_json="{}", score=score,
    )
    ft = MagicMock()
    ft.search.return_value = SimpleNamespace(docs=[doc])
    client = MagicMock()
    client.ft.return_value = ft
    return client


# RediSearch COSINE distance: 0 = identical, 1 = orthogonal, 2 = opposite.
# Corrected conversion (H13): similarity = clamp(1 - distance, 0, 1).
@pytest.mark.parametrize("distance,expected", [(0.0, 1.0), (0.28, 0.72), (1.0, 0.0)])
def test_vector_search_distance_to_similarity(distance, expected):
    client = _fake_client_returning(distance)
    hits = redis_client.vector_search(client, query_vector=[0.0] * 768, top_k=1)
    assert hits[0]["score"] == pytest.approx(expected)


@pytest.mark.parametrize("distance,expected", [(0.0, 1.0), (0.28, 0.72), (1.0, 0.0)])
def test_search_tools_distance_to_similarity(distance, expected):
    client = _fake_client_returning(distance)
    hits = redis_client.search_tools(client, query_vector=[0.0] * 768, top_k=1)
    assert hits[0]["score"] == pytest.approx(expected)


def test_vector_search_similarity_clamped_above_one():
    # Distance > 1 (obtuse angle) must clamp to 0, never go negative.
    client = _fake_client_returning(1.5)
    hits = redis_client.vector_search(client, query_vector=[0.0] * 768, top_k=1)
    assert hits[0]["score"] == 0.0


def test_vector_search_negative_top_k_clamped_to_one():
    client = _fake_client_returning(0.0)
    redis_client.vector_search(client, query_vector=[0.0] * 768, top_k=-5)

    query = client.ft.return_value.search.call_args.args[0]
    assert "KNN 1" in query._query_string
    assert query._num == 1


def test_search_tools_negative_top_k_clamped_to_one():
    client = _fake_client_returning(0.0)
    redis_client.search_tools(client, query_vector=[0.0] * 768, top_k=-5)

    query = client.ft.return_value.search.call_args.args[0]
    assert "KNN 1" in query._query_string
    assert query._num == 1


def test_vector_search_rejects_malformed_source_filter():
    """Regression: source_filter was interpolated into the RediSearch query
    string unvalidated — a value with }| altered query semantics."""
    from unittest.mock import MagicMock

    import pytest as _pytest

    from pipeline.clients.redis_client import vector_search

    client = MagicMock()

    with _pytest.raises(ValueError, match="invalid source filter"):
        vector_search(client, [0.0] * 4, source_filter="nac_docs} | @text:(evil")

    client.ft.assert_not_called()


def test_search_redis_returns_error_envelope_for_bad_source_filter(monkeypatch):
    """Regression: the new source-filter ValueError escaped search_docs/
    ask_docs uncaught under the redis backend — the lancedb path returns
    an [{"error": ...}] envelope for the same input."""
    from unittest.mock import MagicMock

    from mcp_servers import rag

    monkeypatch.setattr(rag, "_redis", MagicMock(), raising=False)
    monkeypatch.setattr(rag, "_ollama", MagicMock(embed_query=lambda q: [0.0]), raising=False)

    def raising_vector_search(*args, **kwargs):
        raise ValueError("invalid source filter: 'Tech-Docs'")

    monkeypatch.setattr(rag, "vector_search", raising_vector_search, raising=False)

    hits = rag._search_redis("query", 5, "Tech-Docs")

    assert hits == [{"error": "invalid source filter: 'Tech-Docs'"}]
