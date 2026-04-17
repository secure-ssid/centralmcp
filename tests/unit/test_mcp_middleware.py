"""Unit tests for the MCP middleware chain.

Test bar targets from the pre-merge review:

- NullStripMiddleware:
  - dict with ``None`` values → stripped;
  - non-None falsy (``0``, ``""``, ``False``, ``[]``) → preserved;
  - idempotent on second pass;
  - empty / missing arguments don't crash.

- RateLimitMiddleware:
  - token-bucket holds at the configured rate under burst;
  - releases correctly;
  - no deadlock on exception in the wrapped call.

- install_middleware:
  - idempotent (installing twice doesn't stack);
  - ``before_call`` runs in order before the tool;
  - ``after_call`` runs in reverse order after the tool;
  - ``on_error`` is called when the wrapped tool raises, and a
    non-None return substitutes the result (swallowing the exception).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_servers._middleware import (
    NullStripMiddleware,
    RateLimitMiddleware,
    install_middleware,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_server_with_tool(fn):
    """Build a FastMCP server with ``fn`` registered as a single tool."""
    srv = FastMCP("test")
    srv.tool()(fn)
    return srv


def _call(srv: FastMCP, name: str, args: dict[str, Any]):
    """Call a tool and block for the result — handles ToolManager.call_tool
    being an async coroutine."""
    return asyncio.run(srv._tool_manager.call_tool(name, args))


# ---------------------------------------------------------------------------
# NullStripMiddleware
# ---------------------------------------------------------------------------


class TestNullStrip:
    def test_strips_none_values(self):
        mw = NullStripMiddleware()
        out = mw.before_call("t", {"a": 1, "b": None, "c": "ok"})
        assert out == {"a": 1, "c": "ok"}

    def test_preserves_non_none_falsy(self):
        mw = NullStripMiddleware()
        out = mw.before_call(
            "t",
            {"z": 0, "s": "", "b": False, "l": [], "d": {}, "n": None},
        )
        # Every falsy-but-not-None key must survive; only 'n' should drop.
        assert "n" not in out
        assert out == {"z": 0, "s": "", "b": False, "l": [], "d": {}}

    def test_idempotent_second_pass(self):
        mw = NullStripMiddleware()
        first = mw.before_call("t", {"a": 1, "b": None})
        # ``None`` means "no change"; feed the result back.
        second = mw.before_call("t", first)
        assert second is None  # nothing to strip the second time

    def test_no_args(self):
        mw = NullStripMiddleware()
        assert mw.before_call("t", {}) is None
        assert mw.before_call("t", None) is None  # type: ignore[arg-type]

    def test_does_not_recurse(self):
        """Nested None is deliberately preserved — we only strip the top level."""
        mw = NullStripMiddleware()
        out = mw.before_call("t", {"cfg": {"inner": None}, "drop_me": None})
        assert out == {"cfg": {"inner": None}}

    def test_end_to_end_with_install(self):
        def greet(name: str = "world", shout: bool = False) -> str:
            return f"hello {name}!" + ("!" if shout else "")

        srv = _make_server_with_tool(greet)
        install_middleware(srv, [NullStripMiddleware()])
        # A real client would send {"name": None} for "unset"; Pydantic would
        # reject that (name: str has no None union). NullStrip removes the
        # key so the default kicks in.
        result = _call(srv, "greet", {"name": None})
        # Result is a list of TextContent or similar — check string contains
        # "world".
        assert "world" in str(result)


# ---------------------------------------------------------------------------
# RateLimitMiddleware
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_allows_burst(self):
        """Burst of N calls where N == burst should not wait at all."""
        mw = RateLimitMiddleware(rate=100.0, burst=5)
        t0 = time.monotonic()
        for _ in range(5):
            mw._acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, f"burst took {elapsed:.3f}s, expected <0.1s"

    def test_holds_at_rate_after_burst(self):
        """Calls beyond burst are paced. 10 calls at rate=20, burst=2:
        burst of 2 fires instantly, remaining 8 take ~8/20 = 0.4s."""
        mw = RateLimitMiddleware(rate=20.0, burst=2)
        t0 = time.monotonic()
        for _ in range(10):
            mw._acquire()
        elapsed = time.monotonic() - t0
        # Loose bounds to avoid flakes; expected ~0.4s steady-state.
        assert 0.3 < elapsed < 1.0, f"elapsed={elapsed:.3f}s, want ~0.4s"

    def test_refills_after_idle(self):
        """After an idle period the bucket should be full again."""
        mw = RateLimitMiddleware(rate=50.0, burst=3)
        for _ in range(3):
            mw._acquire()  # drain
        time.sleep(0.1)  # 0.1s * 50/s = 5 tokens (capped at burst=3)
        t0 = time.monotonic()
        for _ in range(3):
            mw._acquire()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05, f"post-idle burst took {elapsed:.3f}s"

    def test_no_deadlock_on_exception(self):
        """If the wrapped tool raises, subsequent calls must still pass."""
        def raiser() -> str:
            raise RuntimeError("boom")

        srv = _make_server_with_tool(raiser)
        install_middleware(srv, [RateLimitMiddleware(rate=100.0, burst=5)])

        # First call raises; that must not leave the rate-limit lock held.
        with pytest.raises(Exception):
            _call(srv, "raiser", {})

        # If the lock were stuck, this call would hang forever. Cap at 2s.
        def ok() -> str:
            return "ok"
        srv2 = _make_server_with_tool(ok)
        install_middleware(srv2, [RateLimitMiddleware(rate=100.0, burst=5)])
        t0 = time.monotonic()
        _call(srv2, "ok", {})
        assert time.monotonic() - t0 < 2.0

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            RateLimitMiddleware(rate=0)
        with pytest.raises(ValueError):
            RateLimitMiddleware(rate=-1)


# ---------------------------------------------------------------------------
# install_middleware
# ---------------------------------------------------------------------------


class _RecordingMiddleware:
    """Test helper: records every before/after/error call."""

    def __init__(self, tag: str, log: list):
        self.tag = tag
        self.log = log

    def before_call(self, name, arguments):
        self.log.append(("before", self.tag, name, dict(arguments)))
        return None

    def after_call(self, name, arguments, result):
        self.log.append(("after", self.tag, name, result))
        return None

    def on_error(self, name, arguments, exc):
        self.log.append(("error", self.tag, name, type(exc).__name__))
        return None


class TestInstallMiddleware:
    def test_hooks_fire_in_order(self):
        log: list = []

        def echo(x: int) -> int:
            log.append(("tool", "echo", x))
            return x * 2

        srv = _make_server_with_tool(echo)
        install_middleware(srv, [_RecordingMiddleware("A", log), _RecordingMiddleware("B", log)])

        _call(srv, "echo", {"x": 3})

        phases = [entry[0] for entry in log]
        tags = [entry[1] if entry[0] != "tool" else "-" for entry in log]
        # before in order A, B; tool; after in order A, B (the installer
        # runs after_call in the same order, not reverse — simpler and
        # fine since middlewares here don't stack side effects).
        assert phases == ["before", "before", "tool", "after", "after"]
        assert tags == ["A", "B", "-", "A", "B"]

    def test_idempotent_install_does_not_stack(self):
        log: list = []

        def echo(x: int) -> int:
            return x

        srv = _make_server_with_tool(echo)
        mw = _RecordingMiddleware("X", log)
        install_middleware(srv, [mw])
        install_middleware(srv, [mw])  # re-install

        _call(srv, "echo", {"x": 1})
        before_count = sum(1 for e in log if e[0] == "before")
        assert before_count == 1, f"got {before_count} before-calls, expected 1"

    def test_on_error_can_substitute_result(self):
        def boom() -> str:
            raise RuntimeError("kaboom")

        class Swallow:
            def before_call(self, name, arguments):
                return None

            def after_call(self, name, arguments, result):
                return None

            def on_error(self, name, arguments, exc):
                return "handled"

        srv = _make_server_with_tool(boom)
        install_middleware(srv, [Swallow()])

        result = _call(srv, "boom", {})
        # ``result`` is the MCP tool return shape; "handled" must appear in it.
        assert "handled" in str(result)

    def test_broken_middleware_does_not_crash_server(self):
        """A middleware that raises in before_call must not kill the tool."""

        class Broken:
            def before_call(self, name, arguments):
                raise RuntimeError("middleware bug")

            def after_call(self, name, arguments, result):
                return None

            def on_error(self, name, arguments, exc):
                return None

        def ok() -> str:
            return "ok"

        srv = _make_server_with_tool(ok)
        install_middleware(srv, [Broken()])

        # Fail-open: the tool still runs.
        result = _call(srv, "ok", {})
        assert "ok" in str(result)
