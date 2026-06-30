"""Aruba Central REST API client.

Wraps HTTP calls with automatic token refresh and 429/5xx retry+backoff.

Ported from aruba-central-portal/utils/central_api_client.py.
"""

from __future__ import annotations

import email.utils
import logging
import random
import time
from typing import Any, Optional

import httpx

from pipeline.clients.token_manager import TokenManager

logger = logging.getLogger(__name__)

_INITIAL_RETRY_DELAY = 60  # seconds — Central rate-limit window
_MAX_RETRY_DELAY = 300
# 5xx retry uses a much smaller floor — these are usually transient, not
# quota exhaustion. Exponential backoff with jitter.
_SERVER_ERROR_INITIAL_DELAY = 1.0
_SERVER_ERROR_MAX_DELAY = 30.0


def _parse_retry_after(value: str) -> Optional[float]:
    """Parse an HTTP ``Retry-After`` header value.

    The header may be either an integer number of seconds or an HTTP-date
    (RFC 7231 §7.1.3). Returns the wait time in seconds, or ``None`` if
    the value is unparseable.
    """
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    now = time.time()
    target_ts = target.timestamp()
    return max(0.0, target_ts - now)


class CentralClient:
    """HTTP client for Aruba Central REST APIs with token refresh and retry."""

    def __init__(
        self,
        base_url: str,
        token_manager: TokenManager,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_manager = token_manager
        self.session = httpx.Client(timeout=30.0)
        self.session.headers.update({"Content-Type": "application/json"})
        self._refresh_auth_header()

    def _refresh_auth_header(self) -> None:
        token = self.token_manager.get_access_token()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _ensure_valid_token(self) -> None:
        self._refresh_auth_header()

    def _request(
        self,
        method: str,
        endpoint: str,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request, honoring Retry-After on 429 and backing
        off on transient 5xx errors.

        Retry policy:
        - 429: wait ``Retry-After`` if the header is present (clamped to
          ``_MAX_RETRY_DELAY``); otherwise use the legacy 60s → 300s
          1.5× backoff path for compatibility.
        - 502/503/504: exponential backoff (1s → 30s) with ±20% jitter.
        - Any other status: return immediately (callers decide).

        Only idempotent semantics are retried; POST and PATCH are retried
        here only on 429 (the request hasn't been accepted yet — the
        Central gateway rejects before the handler runs) and on 5xx the
        caller opts in with ``retry_5xx=True``.
        """
        # Caller opt-in to retry 5xx on non-GET verbs. GET/HEAD retry 5xx
        # unconditionally because they're safe.
        retry_5xx = kwargs.pop("retry_5xx", None)
        if retry_5xx is None:
            retry_5xx = method.upper() in ("GET", "HEAD")

        url = f"{self.base_url}{endpoint}"
        retry_429_delay = _INITIAL_RETRY_DELAY
        retry_5xx_delay = _SERVER_ERROR_INITIAL_DELAY

        for attempt in range(max_retries + 1):
            self._ensure_valid_token()
            response = self.session.request(method, url, **kwargs)

            if response.status_code == 429 and attempt < max_retries:
                # Prefer the server's hint if present.
                hint = _parse_retry_after(response.headers.get("Retry-After", ""))
                wait = hint if hint is not None else retry_429_delay
                wait = min(wait, _MAX_RETRY_DELAY)
                logger.warning(
                    "Rate limit (429) on %s %s — waiting %.1fs (attempt %d/%d, Retry-After=%r)",
                    method,
                    url,
                    wait,
                    attempt + 1,
                    max_retries,
                    response.headers.get("Retry-After"),
                )
                time.sleep(wait)
                # Grow the no-header fallback so repeated 429s don't
                # hammer the API.
                retry_429_delay = min(int(retry_429_delay * 1.5), _MAX_RETRY_DELAY)
                continue

            if (
                retry_5xx
                and response.status_code in (502, 503, 504)
                and attempt < max_retries
            ):
                jitter = 1.0 + random.uniform(-0.2, 0.2)
                wait = min(retry_5xx_delay * jitter, _SERVER_ERROR_MAX_DELAY)
                logger.warning(
                    "Transient server error %d on %s %s — waiting %.2fs "
                    "(attempt %d/%d)",
                    response.status_code,
                    method,
                    url,
                    wait,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait)
                retry_5xx_delay = min(retry_5xx_delay * 2, _SERVER_ERROR_MAX_DELAY)
                continue

            return response

        return response  # last response after all retries

    def get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        logger.debug(
            "GET %s%s params_keys=%s",
            self.base_url,
            endpoint,
            sorted((params or {}).keys()),
        )
        response = self._request("GET", endpoint, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def post(
        self,
        endpoint: str,
        data: Optional[dict[str, Any] | list[Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug(
            "POST %s%s body_type=%s body_keys=%s",
            self.base_url,
            endpoint,
            type(data).__name__ if data is not None else None,
            sorted(data.keys()) if isinstance(data, dict) else None,
        )
        response = self._request("POST", endpoint, json=data, params=params)
        if not response.is_success:
            raise Exception(
                f"{response.status_code} {response.reason_phrase} — {response.text[:500]}"
            )
        return _parse_json(response)

    def post_async(
        self,
        endpoint: str,
        data: Optional[dict[str, Any] | list[Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> str:
        """POST to an async endpoint; returns the Location header value (task URI)."""
        logger.debug("POST(async) %s%s", self.base_url, endpoint)
        response = self._request("POST", endpoint, json=data, params=params)
        if not response.is_success:
            raise Exception(
                f"{response.status_code} {response.reason_phrase} — {response.text[:500]}"
            )
        location = response.headers.get("Location", "")
        logger.info("POST async Location: %s", location)
        return location

    def patch(
        self,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("PATCH %s%s", self.base_url, endpoint)
        response = self._request("PATCH", endpoint, json=data, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def put(
        self,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("PUT %s%s", self.base_url, endpoint)
        response = self._request("PUT", endpoint, json=data, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def delete(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("DELETE %s%s", self.base_url, endpoint)
        response = self._request("DELETE", endpoint, params=params)
        response.raise_for_status()
        return _parse_json(response)

def _parse_json(response: httpx.Response) -> dict[str, Any]:
    if not response.text or not response.text.strip():
        return {}
    try:
        result = response.json()
        return result if isinstance(result, dict) else {"items": result}
    except ValueError as exc:
        logger.error("Failed to parse JSON: %s (body_len=%d)", exc, len(response.text or ""))
        return {}
