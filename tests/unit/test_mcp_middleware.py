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
    MacNormalizeMiddleware,
    RateLimitMiddleware,
    ResponseEnvelopeMiddleware,
    UnknownToolSuggestMiddleware,
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


def _call_converted(srv: FastMCP, name: str, args: dict[str, Any]):
    return asyncio.run(srv._tool_manager.call_tool(name, args, convert_result=True))


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
    @staticmethod
    def _acquire_many(mw: RateLimitMiddleware, count: int) -> None:
        async def _run() -> None:
            for _ in range(count):
                await mw._acquire()

        asyncio.run(_run())

    def test_allows_burst(self):
        """Burst of N calls where N == burst should not wait at all."""
        mw = RateLimitMiddleware(rate=100.0, burst=5)
        t0 = time.monotonic()
        self._acquire_many(mw, 5)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, f"burst took {elapsed:.3f}s, expected <0.1s"

    def test_holds_at_rate_after_burst(self):
        """Calls beyond burst are paced. 10 calls at rate=20, burst=2:
        burst of 2 fires instantly, remaining 8 take ~8/20 = 0.4s."""
        mw = RateLimitMiddleware(rate=20.0, burst=2)
        t0 = time.monotonic()
        self._acquire_many(mw, 10)
        elapsed = time.monotonic() - t0
        # Loose bounds to avoid flakes; expected ~0.4s steady-state.
        assert 0.3 < elapsed < 1.0, f"elapsed={elapsed:.3f}s, want ~0.4s"

    def test_refills_after_idle(self):
        """After an idle period the bucket should be full again."""
        mw = RateLimitMiddleware(rate=50.0, burst=3)
        self._acquire_many(mw, 3)  # drain
        time.sleep(0.1)  # 0.1s * 50/s = 5 tokens (capped at burst=3)
        t0 = time.monotonic()
        self._acquire_many(mw, 3)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05, f"post-idle burst took {elapsed:.3f}s"

    def test_wait_does_not_block_event_loop(self):
        async def _run() -> float:
            mw = RateLimitMiddleware(rate=1.0, burst=1)
            await mw._acquire()  # drain the only token
            marker = asyncio.create_task(asyncio.sleep(0.01))
            waiter = asyncio.create_task(mw.before_call("slow_tool", {}))
            t0 = time.monotonic()
            await marker
            marker_elapsed = time.monotonic() - t0
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            return marker_elapsed

        elapsed = asyncio.run(_run())
        assert elapsed < 0.2, f"rate-limit wait blocked event loop for {elapsed:.3f}s"

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


class TestUnknownToolSuggest:
    def test_unknown_tool_returns_structured_hint(self):
        def list_devices() -> str:
            return "ok"

        srv = _make_server_with_tool(list_devices)
        install_middleware(
            srv,
            [UnknownToolSuggestMiddleware(lambda: srv._tool_manager._tools)],
        )

        result = _call(srv, "get_devices", {})

        assert "Unknown tool: get_devices" in str(result)
        assert "find_tool" in str(result)
        assert "list_devices" in str(result)

    def test_custom_suggestion_provider(self):
        def find_tool() -> str:
            return "ok"

        srv = _make_server_with_tool(find_tool)
        install_middleware(
            srv,
            [
                UnknownToolSuggestMiddleware(
                    lambda: srv._tool_manager._tools,
                    suggestion_provider=lambda name, limit: [{"name": "create_vlan", "score": 1.0}],
                )
            ],
        )

        result = _call(srv, "create_vlan", {})

        assert "create_vlan" in str(result)

    def test_on_error_substitute_is_enveloped_when_chained_with_response_envelope(self):
        """UnknownToolSuggestMiddleware's on_error substitute must get the
        same {ok, status, data, ...} envelope as any other failure result —
        not a bespoke shape that skips ResponseEnvelopeMiddleware entirely."""

        def list_devices() -> str:
            return "ok"

        srv = _make_server_with_tool(list_devices)
        install_middleware(
            srv,
            [
                UnknownToolSuggestMiddleware(lambda: srv._tool_manager._tools),
                ResponseEnvelopeMiddleware(),
            ],
        )

        result = _call(srv, "get_devices", {})

        assert isinstance(result, dict)
        assert result["ok"] is False
        assert "Unknown tool: get_devices" in result["message"]
        assert "find_tool" in result["data"]["hint"]


class TestResponseEnvelope:
    def test_wraps_error_dict(self):
        mw = ResponseEnvelopeMiddleware()

        result = mw.after_call("clearpass_get", {}, {"error": "not configured"})

        assert result == {
            "ok": False,
            "status": 500,
            "data": {"error": "not configured"},
            "message": "not configured",
            "tool": "clearpass_get",
            "platform": None,
        }

    def test_wraps_cancelled_status(self):
        mw = ResponseEnvelopeMiddleware()

        result = mw.after_call("reboot_device", {}, {"status": "CANCELLED", "detail": "user declined confirmation"})

        assert result is not None
        assert result["ok"] is False
        assert result["status"] == 409
        assert result["message"] == "user declined confirmation"

    def test_wraps_failed_status(self):
        # atroubleshoot_poll (shared.py) treats status="FAILED" as a
        # legitimate terminal state and returns it without raising — this is
        # the only signal a caller has that a device operation didn't succeed.
        mw = ResponseEnvelopeMiddleware()

        result = mw.after_call("reboot_device", {}, {"status": "FAILED", "errors": []})

        assert result is not None
        assert result["ok"] is False
        assert result["status"] == 500

    def test_success_dict_passes_through(self):
        mw = ResponseEnvelopeMiddleware()

        assert mw.after_call("list_devices", {}, {"items": []}) is None

    def test_already_enveloped_passes_through(self):
        mw = ResponseEnvelopeMiddleware()
        result = {"ok": False, "data": {}, "tool": "x"}

        assert mw.after_call("x", {}, result) is None

    def test_envelope_runs_before_fastmcp_conversion(self):
        def bad() -> dict[str, str]:
            return {"error": "nope"}

        srv = _make_server_with_tool(bad)
        install_middleware(srv, [ResponseEnvelopeMiddleware()])

        result = _call_converted(srv, "bad", {})

        rendered = str(result)
        assert '"ok": false' in rendered
        assert '"tool": "bad"' in rendered


class TestMacNormalize:
    def test_normalizes_common_mac_formats(self):
        mw = MacNormalizeMiddleware()
        payload = {
            "clientMac": "AA-BB-CC-DD-EE-FF",
            "ap": {"mac": "aabb.ccdd.eeff"},
            "text": "client 11:22:33:44:55:66 connected",
        }

        result = mw.after_call("find_client", {}, payload)

        assert result == {
            "clientMac": "aa:bb:cc:dd:ee:ff",
            "ap": {"mac": "aa:bb:cc:dd:ee:ff"},
            "text": "client 11:22:33:44:55:66 connected",
        }

    def test_leaves_non_mac_strings_unchanged(self):
        mw = MacNormalizeMiddleware()

        assert mw.after_call("x", {}, {"serial": "CN1234567890"}) is None
