"""RateLimitMiddleware — token-bucket cap on tool call rate.

Central applies a **10 requests per second account-wide** limit (source:
https://developer.arubanetworks.com/new-central/docs/getting-started-with-rest-apis).
The limit is shared across *all* tokens for the same account, so our 5+
MCP servers plus any human scripts all draw from the same bucket.

To stay comfortably under the cap, the default rate here is 8/s — the
remaining ~2/s headroom absorbs transient bursts (a handful of tool
calls issued in parallel by a Claude client running multiple subagents).

Uses a simple token bucket: tokens refill at ``rate`` per second up to
``burst`` max. On empty bucket we await the earliest refill time without
blocking the asyncio event loop.
This is **per-process**, so if multiple server processes run on the same
host each has its own bucket. That's fine — we still cut peak rate
roughly by the number of processes, which is the real blast-radius
concern. A fully-correct shared limiter would need a cross-process
coordinator (Redis / file lock) and isn't worth the complexity here.
"""

from __future__ import annotations

import logging
import asyncio
import time
from typing import Any

logger = logging.getLogger(__name__)


class RateLimitMiddleware:
    """Token-bucket rate limiter around every tool call."""

    def __init__(self, rate: float = 8.0, burst: int | None = None):
        """
        Args:
            rate: Steady-state token refill rate, tokens per second.
            burst: Max tokens in the bucket. Defaults to ``max(2, int(rate))``
                so short bursts up to ``rate`` calls can fire immediately.
        """
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        self.rate = rate
        self.burst = burst if burst is not None else max(2, int(rate))
        self._tokens: float = float(self.burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def _acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until we'd have 1 token?
                wait = (1.0 - self._tokens) / self.rate
            # Sleep outside the lock so other calls can refill too.
            logger.debug("rate limit: sleeping %.3fs", wait)
            await asyncio.sleep(wait)

    async def before_call(self, name: str, arguments: dict[str, Any]) -> None:
        await self._acquire()
        return None

    def after_call(self, name: str, arguments: dict[str, Any], result: Any) -> None:
        return None

    def on_error(self, name: str, arguments: dict[str, Any], exc: BaseException) -> None:
        return None
