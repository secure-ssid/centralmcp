from unittest.mock import patch

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
