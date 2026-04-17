"""Middleware installer for ``mcp.server.fastmcp`` tool managers.

Monkey-patches ``ToolManager.call_tool`` to run each installed
middleware's ``before_call`` and ``after_call`` hooks. This is a
deliberate trade: the shim has no middleware API, we don't want a new
dependency, and patching a single method keeps blast radius small.

Note on async: ``ToolManager.call_tool`` returns a coroutine — the
installer wraps it as ``async def`` so middleware runs synchronously in
the event-loop-blocking happy path (HTTP is blocking requests anyway).
Middleware hooks themselves are **sync only**; if they need to I/O, they
should do it fast. This keeps the middleware API trivial to test.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


class Middleware(Protocol):
    """Minimal middleware surface.

    Either hook may mutate ``arguments`` / return a new ``result``. If a
    hook raises, the error is logged and swallowed so a broken middleware
    can't crash the server (fail-open).
    """

    def before_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Mutate / replace call arguments. Return ``None`` to leave args unchanged."""
        ...

    def after_call(self, name: str, arguments: dict[str, Any], result: Any) -> Any:
        """Mutate / replace the tool result. Return ``None`` to leave result unchanged."""
        ...

    def on_error(self, name: str, arguments: dict[str, Any], exc: BaseException) -> Any:
        """Called when the wrapped tool raises. Return a value to swallow + substitute.
        Return ``None`` (default) to let the exception propagate."""
        ...


_INSTALLED_ATTR = "_centralmcp_middleware_original"


def _run_before(middlewares, name, args):
    for mw in middlewares:
        try:
            before = getattr(mw, "before_call", None)
            if before is not None:
                new_args = before(name, args)
                if new_args is not None:
                    args = new_args
        except Exception as exc:
            logger.warning("middleware %s.before_call failed: %s", type(mw).__name__, exc)
    return args


def _run_after(middlewares, name, args, result):
    for mw in middlewares:
        try:
            after = getattr(mw, "after_call", None)
            if after is not None:
                new_result = after(name, args, result)
                if new_result is not None:
                    result = new_result
        except Exception as exc:
            logger.warning("middleware %s.after_call failed: %s", type(mw).__name__, exc)
    return result


def _run_on_error(middlewares, name, args, exc):
    for mw in middlewares:
        try:
            handler = getattr(mw, "on_error", None)
            if handler is None:
                continue
            substitute = handler(name, args, exc)
            if substitute is not None:
                return substitute
        except Exception as handler_exc:
            logger.warning(
                "middleware %s.on_error failed: %s", type(mw).__name__, handler_exc
            )
    return None


def install_middleware(server: FastMCP, middlewares: list[Middleware]) -> None:
    """Install ``middlewares`` on ``server``'s tool manager.

    Idempotent — a server that already has middleware installed will have
    its chain *replaced* rather than stacked, so re-imports in tests
    don't accumulate.
    """
    tm = server._tool_manager
    # Preserve the *real* original so re-install doesn't stack.
    original = getattr(tm, _INSTALLED_ATTR, None) or tm.call_tool
    if not getattr(tm, _INSTALLED_ATTR, None):
        setattr(tm, _INSTALLED_ATTR, original)

    async def wrapped_call_tool(name, arguments, context=None, convert_result=False):  # noqa: ANN001,ANN202
        args = dict(arguments) if arguments else {}
        args = _run_before(middlewares, name, args)

        try:
            result = await original(name, args, context=context, convert_result=convert_result)
        except BaseException as exc:
            substitute = _run_on_error(middlewares, name, args, exc)
            if substitute is not None:
                return substitute
            raise

        return _run_after(middlewares, name, args, result)

    tm.call_tool = wrapped_call_tool  # type: ignore[method-assign]
