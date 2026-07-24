"""Aruba Central REST API client.

Wraps HTTP calls with automatic token refresh and 429/5xx retry+backoff.

Ported from aruba-central-portal/utils/central_api_client.py.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from pipeline.clients.token_manager import TokenManager

logger = logging.getLogger(__name__)


def _post_error(response: httpx.Response) -> Exception:
    """Build the error raised for a failed POST.

    Attaches ``.response`` — mirroring httpx.HTTPStatusError — so callers
    doing ``getattr(exc, "response", None).text`` see the real body.
    """
    exc = Exception(f"{response.status_code} {response.reason_phrase} — {response.text[:500]}")
    exc.response = response  # type: ignore[attr-defined]
    return exc


def error_body(exc: Exception) -> str:
    """Response body text from an HTTP-error exception, or "" if there is none."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return ""
    return getattr(resp, "text", "") or ""


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


@dataclass(frozen=True)
class RateLimitStatus:
    """Parsed rate-limit response metadata (RateLimit-* / X-RateLimit-*)."""

    limit: Optional[int]
    remaining: Optional[int]
    reset_seconds: Optional[float]
    raw_reset: Optional[str]


@dataclass(frozen=True)
class DeprecationStatus:
    """Parsed API-deprecation response metadata (Deprecation / Sunset / Link)."""

    deprecation: Optional[str]
    sunset: Optional[str]
    link: Optional[str]


def _parse_int_header(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _extract_rate_limit(headers: Any) -> Optional[RateLimitStatus]:
    """Parse rate-limit headers, preferring the IETF draft names with a
    fallback to the older ``X-RateLimit-*`` convention some gateways use."""
    limit = headers.get("RateLimit-Limit") or headers.get("X-RateLimit-Limit")
    remaining = headers.get("RateLimit-Remaining") or headers.get("X-RateLimit-Remaining")
    reset = headers.get("RateLimit-Reset") or headers.get("X-RateLimit-Reset")
    if limit is None and remaining is None and reset is None:
        return None
    return RateLimitStatus(
        limit=_parse_int_header(limit),
        remaining=_parse_int_header(remaining),
        reset_seconds=_parse_retry_after(reset) if reset else None,
        raw_reset=reset,
    )


def _extract_deprecation(headers: Any) -> Optional[DeprecationStatus]:
    """Parse RFC 8594 ``Deprecation``/``Sunset`` headers plus a ``Link``
    header carrying a deprecation-notice URL, if Central sends one."""
    deprecation = headers.get("Deprecation")
    sunset = headers.get("Sunset")
    link = headers.get("Link")
    if not deprecation and not sunset:
        return None
    return DeprecationStatus(deprecation=deprecation, sunset=sunset, link=link)


class CentralClient:
    """HTTP client for Aruba Central REST APIs with token refresh and retry."""

    def __init__(
        self,
        base_url: str,
        token_manager: TokenManager,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_manager = token_manager
        self.timeout = 30.0
        self.session = httpx.Client(timeout=self.timeout)
        self.session.headers.update({"Content-Type": "application/json"})
        # Generation observed at the moment the current Authorization header
        # was set — passed back to TokenManager on a 401 retry so concurrent
        # 401s against the same stale token collapse into one real refresh
        # instead of one per request. See TokenManager.get_access_token().
        self._token_generation = 0
        # Most recent rate-limit / deprecation response metadata, updated on
        # every response (success or failure). Side-channel only — never
        # merged into a tool's returned JSON, so existing callers' response
        # shapes are unaffected. Domain tools may read these opportunistically.
        self.last_rate_limit: Optional[RateLimitStatus] = None
        self.last_deprecation: Optional[DeprecationStatus] = None
        self._refresh_auth_header()

    def _refresh_auth_header(self) -> None:
        token = self.token_manager.get_access_token()
        self._token_generation = self.token_manager.generation
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _ensure_valid_token(self) -> None:
        self._refresh_auth_header()

    def _record_response_metadata(self, response: httpx.Response, endpoint: str) -> None:
        """Capture rate-limit / deprecation metadata from ``response`` and
        log a warning the first time a deprecated endpoint is hit in a
        given process (avoids per-call log spam on hot paths)."""
        rate_limit = _extract_rate_limit(response.headers)
        if rate_limit is not None:
            self.last_rate_limit = rate_limit
        deprecation = _extract_deprecation(response.headers)
        if deprecation is not None:
            self.last_deprecation = deprecation
            logger.warning(
                "Deprecated endpoint called: %s (Deprecation=%r Sunset=%r Link=%r)",
                endpoint,
                deprecation.deprecation,
                deprecation.sunset,
                deprecation.link,
            )

    def rate_limit_status(self) -> Optional[dict[str, Any]]:
        """Most recent rate-limit metadata as a plain dict, or ``None`` if
        no response has carried rate-limit headers yet."""
        if self.last_rate_limit is None:
            return None
        return {
            "limit": self.last_rate_limit.limit,
            "remaining": self.last_rate_limit.remaining,
            "reset_seconds": self.last_rate_limit.reset_seconds,
            "raw_reset": self.last_rate_limit.raw_reset,
        }

    def deprecation_status(self) -> Optional[dict[str, Any]]:
        """Most recent API-deprecation metadata as a plain dict, or ``None``
        if no response has carried deprecation headers yet."""
        if self.last_deprecation is None:
            return None
        return {
            "deprecation": self.last_deprecation.deprecation,
            "sunset": self.last_deprecation.sunset,
            "link": self.last_deprecation.link,
        }

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
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            from mcp_servers.shared import platform_writes_allowed

            if not platform_writes_allowed("central"):
                raise PermissionError(
                    "Central write requests are disabled. "
                    "Set CENTRALMCP_CENTRAL_WRITES=1 to enable them."
                )

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
            self._record_response_metadata(response, endpoint)

            if response.status_code == 401 and attempt < max_retries:
                logger.warning(
                    "Unauthorized (401) on %s %s — forcing token refresh (attempt %d/%d)",
                    method,
                    url,
                    attempt + 1,
                    max_retries,
                )
                # Pass the generation observed when this request's token was
                # set — if another thread already refreshed since then, this
                # collapses into a no-op check instead of a redundant fetch.
                self.token_manager.get_access_token(
                    force_refresh=True, observed_generation=self._token_generation
                )
                self._refresh_auth_header()
                continue

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

    async def _arequest(
        self,
        method: str,
        endpoint: str,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> httpx.Response:
        """Async counterpart to ``_request`` for MCP tools running on an event loop."""
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            from mcp_servers.shared import platform_writes_allowed

            if not platform_writes_allowed("central"):
                raise PermissionError(
                    "Central write requests are disabled. "
                    "Set CENTRALMCP_CENTRAL_WRITES=1 to enable them."
                )

        retry_5xx = kwargs.pop("retry_5xx", None)
        if retry_5xx is None:
            retry_5xx = method.upper() in ("GET", "HEAD")

        url = f"{self.base_url}{endpoint}"
        retry_429_delay = _INITIAL_RETRY_DELAY
        retry_5xx_delay = _SERVER_ERROR_INITIAL_DELAY
        extra_headers = kwargs.pop("headers", None)

        async with httpx.AsyncClient(timeout=self.timeout) as session:
            for attempt in range(max_retries + 1):
                token, generation = await asyncio.to_thread(
                    self.token_manager.get_access_token_with_generation
                )
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
                if extra_headers:
                    headers.update(extra_headers)

                response = await session.request(method, url, headers=headers, **kwargs)
                self._record_response_metadata(response, endpoint)

                if response.status_code == 401 and attempt < max_retries:
                    logger.warning(
                        "Unauthorized (401) on %s %s — forcing token refresh (attempt %d/%d)",
                        method,
                        url,
                        attempt + 1,
                        max_retries,
                    )
                    # Same generation-aware collapse as the sync path — see
                    # TokenManager.get_access_token()'s observed_generation.
                    await asyncio.to_thread(
                        self.token_manager.get_access_token,
                        True,
                        observed_generation=generation,
                    )
                    continue

                if response.status_code == 429 and attempt < max_retries:
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
                    await asyncio.sleep(wait)
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
                    await asyncio.sleep(wait)
                    retry_5xx_delay = min(retry_5xx_delay * 2, _SERVER_ERROR_MAX_DELAY)
                    continue

                return response

        return response

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

    async def aget(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        logger.debug(
            "GET(async) %s%s params_keys=%s",
            self.base_url,
            endpoint,
            sorted((params or {}).keys()),
        )
        response = await self._arequest("GET", endpoint, params=params)
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
            raise _post_error(response)
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
            raise _post_error(response)
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
