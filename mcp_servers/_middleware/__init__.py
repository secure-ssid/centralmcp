"""Lightweight middleware for centralmcp FastMCP servers.

The ``mcp.server.fastmcp`` shim we depend on has no first-class middleware
API (only ``add_tool``/``add_resource``/``add_prompt``). We achieve the
same effect by monkey-patching ``ToolManager.call_tool`` so pre- and
post-processing hooks compose around every tool invocation.

Design goals:
- Zero new dependencies.
- Idempotent ``install_middleware()`` — safe to call multiple times.
- Each middleware is an async-aware callable or a plain class with
  ``before_call(name, arguments)`` / ``after_call(name, arguments, result)``.
- Fail-open: if a middleware raises, the call still proceeds (the
  exception is logged and swallowed) so a bad middleware can't take the
  whole server down.

See individual modules for details:

- :mod:`mcp_servers._middleware.null_strip` — strip explicit ``None``
  argument values before tool call. Ported from
  ``nowireless4u/hpe-networking-mcp`` (MIT).
- :mod:`mcp_servers._middleware.rate_limit` — token-bucket limiter to
  keep total call rate under the Central account-wide cap (10/s).

Retry lives one layer down in
:mod:`pipeline.clients.central_client` (``_request`` honors
``Retry-After`` on 429 and backs off on 502/503/504). Per-tool retry
would be wrong for async-poll tools (``cx_ping`` et al.) — a 5xx
mid-poll would restart the whole ping instead of resuming.
"""

from __future__ import annotations

from .install import Middleware, install_middleware
from .null_strip import NullStripMiddleware
from .rate_limit import RateLimitMiddleware

__all__ = [
    "Middleware",
    "NullStripMiddleware",
    "RateLimitMiddleware",
    "install_middleware",
]
