from __future__ import annotations

import concurrent.futures
import threading
import time

from pipeline.clients.token_manager import TokenManager


class _TokenResponse:
    def __init__(self, token: str):
        self._token = token

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"access_token": self._token, "expires_in": 7200}


def test_token_manager_deduplicates_concurrent_refreshes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_CACHE_DIR", str(tmp_path))
    calls: list[dict[str, object]] = []
    call_lock = threading.Lock()

    def fake_post(url, data=None, headers=None, timeout=None):
        with call_lock:
            calls.append(
                {
                    "url": url,
                    "data": data,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
        time.sleep(0.02)
        return _TokenResponse("fresh-token")

    monkeypatch.setattr("pipeline.clients.token_manager.httpx.post", fake_post)

    manager = TokenManager(
        client_id="client-id",
        client_secret="secret",
        token_url="https://sso.example.com/token",
        cache_key="concurrent",
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(lambda _: manager.get_access_token(), range(8)))

    assert tokens == ["fresh-token"] * 8
    assert len(calls) == 1
    assert calls[0]["url"] == "https://sso.example.com/token"


def test_token_manager_force_refresh_still_refreshes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_CACHE_DIR", str(tmp_path))
    tokens = iter(["token-1", "token-2"])

    def fake_post(url, data=None, headers=None, timeout=None):
        return _TokenResponse(next(tokens))

    monkeypatch.setattr("pipeline.clients.token_manager.httpx.post", fake_post)

    manager = TokenManager(
        client_id="client-id",
        client_secret="secret",
        token_url="https://sso.example.com/token",
        cache_key="force",
    )

    assert manager.get_access_token() == "token-1"
    assert manager.get_access_token(force_refresh=True) == "token-2"
