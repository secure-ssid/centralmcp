"""Opt-in append-only audit records for router tool calls.

Set ``CENTRALMCP_AUDIT_LOG=1`` to write ``state/tool-audit.jsonl`` or set it
to an explicit file path. Records contain argument keys and a digest of a
redacted copy, never raw argument or result values.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_servers.shared import redact_sensitive

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_PATH = ROOT / "state" / "tool-audit.jsonl"
_AUDIT_ENV = "CENTRALMCP_AUDIT_LOG"


def audit_path() -> Path | None:
    raw = os.getenv(_AUDIT_ENV, "").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off"}:
        return None
    if raw.lower() in {"1", "true", "yes", "on"}:
        return DEFAULT_AUDIT_PATH
    return Path(raw).expanduser()


def _argument_digest(arguments: dict[str, Any]) -> str:
    redacted = redact_sensitive(arguments)
    canonical = json.dumps(
        redacted,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _outcome(result: Any) -> str:
    if not isinstance(result, dict):
        return "success"
    if result.get("ok") is False or result.get("error"):
        return "error"
    status = str(result.get("status") or "").strip().lower()
    if status in {
        "blocked",
        "cancelled",
        "declined",
        "confirmation_required",
        "forbidden",
        "failed",
        "failure",
        "error",
    }:
        return status
    return "success"


class AuditLogMiddleware:
    """Write one redacted JSONL record per completed or failed tool call."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._starts: contextvars.ContextVar[list[float]] = contextvars.ContextVar(
            f"centralmcp_audit_starts_{id(self)}",
            default=[],
        )
        self._write_lock = threading.Lock()

    def _configured_path(self) -> Path | None:
        return self.path or audit_path()

    def before_call(self, name: str, arguments: dict[str, Any]) -> None:
        if self._configured_path() is None:
            return None
        starts = list(self._starts.get())
        starts.append(time.monotonic())
        self._starts.set(starts)
        return None

    def _duration_ms(self) -> float | None:
        starts = list(self._starts.get())
        if not starts:
            return None
        started = starts.pop()
        self._starts.set(starts)
        return round((time.monotonic() - started) * 1000, 3)

    def _write(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        outcome: str,
        error_type: str | None = None,
    ) -> None:
        path = self._configured_path()
        if path is None:
            return
        target = arguments.get("name") if name in {"invoke_tool", "invoke_read_tool"} else None
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "tool": name,
            "target_tool": str(target) if target else None,
            "argument_keys": sorted(str(key) for key in arguments),
            "argument_digest": _argument_digest(arguments),
            "outcome": outcome,
            "duration_ms": self._duration_ms(),
            "error_type": error_type,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def after_call(
        self,
        name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> None:
        self._write(name, arguments, outcome=_outcome(result))
        return None

    def on_error(
        self,
        name: str,
        arguments: dict[str, Any],
        exc: BaseException,
    ) -> None:
        self._write(
            name,
            arguments,
            outcome="exception",
            error_type=type(exc).__name__,
        )
        return None
