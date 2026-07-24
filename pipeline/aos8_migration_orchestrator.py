"""Atomic, resumable orchestration for AOS8-to-Central migration candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.aos8_target_adapters import (
    BaseCentralTargetAdapter,
    ConflictPolicy,
    TargetContext,
    TargetType,
    WriteGateError,
)

MAX_CANDIDATES = 500
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_RESULT_ITEMS = 50
MAX_HISTORY_ITEMS = 10
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(?:credential|key|passphrase|password|psk|"
    r"secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_SAFE_SECRET_METADATA_KEYS = {
    "requires_secret_input",
    "required_secret_names",
    "secret_fields",
    "secrets_persisted",
}
# Presence-only boolean flags emitted by `pipeline/aos8_migration.py`
# (`_wlan_security_intent`'s `security.passphrase_present` /
# `security.psk_hexkey_present`). They never carry secret material -- only
# whether a credential field was populated in the AOS8 source -- but their
# names trip `_SENSITIVE_KEY_RE`'s "passphrase"/"psk" tokens. They are
# allowlisted by exact name *and* gated on an actual `bool` value in
# `_is_presence_metadata` below, so a same-named field holding a real secret
# string would still be redacted.
_PRESENCE_ONLY_BOOLEAN_METADATA_KEYS = {
    "passphrase_present",
    "psk_hexkey_present",
}
_TERMINAL_SUCCESS = {"applied", "skipped"}
_TERMINAL = {*_TERMINAL_SUCCESS, "unsupported"}


class MigrationRunError(ValueError):
    """Base error for migration-run validation or persistence."""


class MigrationRunNotFoundError(MigrationRunError):
    """The requested migration run does not exist."""


class MalformedMigrationStateError(MigrationRunError):
    """A persisted migration run cannot be decoded safely."""


AdapterFactory = Callable[[TargetContext], BaseCentralTargetAdapter]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def validate_run_id(run_id: str) -> str:
    """Validate a run identifier before deriving any state path."""
    value = str(run_id).strip()
    if (
        not _RUN_ID_RE.fullmatch(value)
        or ".." in value
        or "/" in value
        or "\\" in value
    ):
        raise MigrationRunError(
            "run_id must be 1-64 characters using only letters, numbers, '.', '_', "
            "or '-', and may not contain path traversal"
        )
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if normalized in _SAFE_SECRET_METADATA_KEYS:
        return False
    return bool(
        _SENSITIVE_KEY_RE.search(normalized)
        or normalized.endswith(
            (
                "apikey",
                "credential",
                "credentials",
                "passphrase",
                "passwd",
                "password",
                "privatekey",
                "psk",
                "pwd",
                "secret",
                "sharedkey",
                "token",
            )
        )
        or normalized in {"community_string", "snmp_read", "snmp_write"}
    )


def _is_presence_metadata(key: Any, value: Any) -> bool:
    """Return True only for a known presence-only boolean metadata field.

    Narrow by design: both the exact (normalized) key name must be
    allowlisted *and* the value must actually be a `bool`. This is
    intentionally not a suffix/prefix exception -- a field sharing one of
    these names but holding a non-bool (e.g. an actual secret string) is
    still redacted by `_is_sensitive_key`.
    """
    return (
        isinstance(value, bool)
        and _normalized_key(key) in _PRESENCE_ONLY_BOOLEAN_METADATA_KEYS
    )


def _sanitize(
    value: Any,
    *,
    secret_values: Iterable[str] = (),
    max_depth: int = 8,
    _depth: int = 0,
) -> Any:
    secrets = tuple(secret for secret in secret_values if secret)
    if _depth >= max_depth:
        return "<bounded:max-depth>"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:MAX_RESULT_ITEMS]:
            key = str(raw_key)
            out[key] = (
                item
                if _is_presence_metadata(key, item)
                else "******"
                if _is_sensitive_key(key)
                else _sanitize(
                    item,
                    secret_values=secrets,
                    max_depth=max_depth,
                    _depth=_depth + 1,
                )
            )
        if len(value) > MAX_RESULT_ITEMS:
            out["_bounded"] = {
                "total_keys": len(value),
                "returned_keys": MAX_RESULT_ITEMS,
            }
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        bounded = [
            _sanitize(
                item,
                secret_values=secrets,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for item in items[:MAX_RESULT_ITEMS]
        ]
        if len(items) > MAX_RESULT_ITEMS:
            bounded.append(
                {
                    "_bounded": {
                        "total_items": len(items),
                        "returned_items": MAX_RESULT_ITEMS,
                    }
                }
            )
        return bounded
    if isinstance(value, str):
        text = value
        for secret in secrets:
            text = text.replace(secret, "******")
        if len(text) > 1000:
            return f"{text[:1000]}... [truncated {len(text) - 1000} chars]"
        return text
    return value


def _redact_full(value: Any, *, _depth: int = 0) -> Any:
    if _depth >= 12:
        raise MigrationRunError("Migration candidate nesting exceeds the safe limit.")
    if isinstance(value, Mapping):
        return {
            str(key): (
                item
                if _is_presence_metadata(key, item)
                else "******"
                if _is_sensitive_key(key)
                else _redact_full(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_full(item, _depth=_depth + 1) for item in value]
    if isinstance(value, set):
        return sorted(_redact_full(item, _depth=_depth + 1) for item in value)
    return value


def _safe_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    safe = _redact_full(candidate)
    if not isinstance(safe, dict):
        raise MigrationRunError("Each migration candidate must be an object.")
    return safe


def _candidate_key(candidate: Mapping[str, Any]) -> str:
    object_type = str(candidate.get("object_type", "")).strip()
    identifier = str(candidate.get("identifier", "")).strip()
    if not object_type or not identifier:
        raise MigrationRunError(
            "Each migration candidate requires non-empty object_type and identifier."
        )
    return f"{object_type}:{identifier}"


def _required_secret_names(candidate: Mapping[str, Any]) -> list[str]:
    if not candidate.get("requires_secret_input"):
        return []
    if candidate.get("object_type") == "auth_server":
        return ["shared_secret"]
    names = {
        _normalized_key(str(path).split(".")[-1].split("[", 1)[0])
        for path in candidate.get("secret_fields", [])
    }
    return sorted(name for name in names if name) or ["target_secret"]


def _placeholder_secret_inputs(
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    return {
        _candidate_key(candidate): {
            name: "__runtime_secret_placeholder__"
            for name in _required_secret_names(candidate)
        }
        for candidate in candidates
        if candidate.get("requires_secret_input")
    }


def _target_context(
    target: Mapping[str, Any],
    *,
    secret_inputs: Mapping[str, Mapping[str, str]] | None = None,
) -> TargetContext:
    try:
        return TargetContext(
            target_type=TargetType(str(target["type"])),
            scope_id=target.get("scope_id"),
            scope_name=target.get("scope_name"),
            persona=target.get("persona"),
            cluster_name=target.get("cluster_name"),
            cluster_scope_id=target.get("cluster_scope_id"),
            gateway_name=target.get("gateway_name"),
            gateway_scope_id=target.get("gateway_scope_id"),
            conflict_policy=ConflictPolicy(
                str(target.get("conflict_policy", ConflictPolicy.FAIL.value))
            ),
            secret_inputs=secret_inputs or {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MigrationRunError(f"Invalid persisted target context: {exc}") from exc


def _run_fingerprint(
    candidates: list[dict[str, Any]],
    target: Mapping[str, Any],
    selected: Iterable[str] | None,
) -> str:
    material = {
        "candidates": candidates,
        "target": dict(target),
        "selected": sorted(selected or ()),
    }
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


class MigrationRunStore:
    """Per-run JSON state under ``state/``, persisted by atomic replacement."""

    _run_locks_guard = threading.Lock()
    _run_locks: dict[tuple[str, str], threading.RLock] = {}

    def __init__(self, state_dir: str | Path = "state/aos8_migrations") -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, run_id: str) -> Path:
        validated = validate_run_id(run_id)
        path = (self.state_dir / f"{validated}.json").resolve()
        if path.parent != self.state_dir:
            raise MigrationRunError("run_id resolved outside the migration state directory")
        return path

    @contextmanager
    def lock_run(self, run_id: str) -> Iterator[None]:
        """Serialize state transitions for one run across store instances."""
        validated = validate_run_id(run_id)
        key = (str(self.state_dir), validated)
        with self._run_locks_guard:
            lock = self._run_locks.setdefault(key, threading.RLock())
        with lock:
            yield

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.path_for(run_id)
        if not path.exists():
            raise MigrationRunNotFoundError(f"Migration run {run_id!r} was not found.")
        try:
            if path.stat().st_size > MAX_STATE_BYTES:
                raise MalformedMigrationStateError(
                    f"Migration run {run_id!r} exceeds the state-size limit."
                )
            value = json.loads(path.read_text(encoding="utf-8"))
        except MalformedMigrationStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MalformedMigrationStateError(
                f"Migration run {run_id!r} is malformed: {exc}"
            ) from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("run_id") != validate_run_id(run_id)
            or not isinstance(value.get("candidates"), list)
        ):
            raise MalformedMigrationStateError(
                f"Migration run {run_id!r} has an invalid state schema."
            )
        return value

    def save(self, run: Mapping[str, Any]) -> None:
        run_id = validate_run_id(str(run.get("run_id", "")))
        payload = _canonical_json(run).encode("utf-8")
        if len(payload) > MAX_STATE_BYTES:
            raise MigrationRunError(
                f"Migration run {run_id!r} exceeds the {MAX_STATE_BYTES}-byte state limit."
            )
        destination = self.path_for(run_id)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.new"
        )
        with self._lock:
            try:
                with temporary.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                try:
                    directory_fd = os.open(self.state_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
            finally:
                if temporary.exists():
                    temporary.unlink()

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        paths = sorted(
            self.state_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        summaries: list[dict[str, Any]] = []
        malformed: list[dict[str, str]] = []
        for path in paths:
            try:
                run = self.load(path.stem)
            except MigrationRunError as exc:
                if len(malformed) < MAX_RESULT_ITEMS:
                    malformed.append({"run_id": path.stem, "error": str(exc)})
                continue
            summaries.append(_run_summary(run))
        bounded_limit = max(1, min(int(limit), 100))
        bounded_offset = max(0, int(offset))
        return {
            "runs": summaries[bounded_offset : bounded_offset + bounded_limit],
            "pagination": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": len(summaries),
                "truncated": bounded_offset + bounded_limit < len(summaries),
            },
            "malformed_state_count": len(malformed),
            "malformed_states": malformed[:10],
        }


def _status_counts(run: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in run.get("candidates", []):
        status = str(entry.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _refresh_run_status(run: dict[str, Any]) -> None:
    statuses = [str(entry.get("status", "pending")) for entry in run["candidates"]]
    if statuses and all(status in _TERMINAL for status in statuses):
        run["status"] = (
            "completed_with_issues"
            if "unsupported" in statuses
            else "completed"
        )
    elif "failed" in statuses:
        run["status"] = (
            "partial" if any(status in _TERMINAL_SUCCESS for status in statuses) else "failed"
        )
    elif any(status in _TERMINAL_SUCCESS for status in statuses):
        run["status"] = "partial"
    elif run.get("dry_run_attempted_at"):
        run["status"] = "dry-run-complete"
    else:
        run["status"] = "pending"
    run["updated_at"] = _now()


def _run_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "target": run.get("target"),
        "candidate_count": len(run.get("candidates", [])),
        "status_counts": _status_counts(run),
        "dry_run_attempted_at": run.get("dry_run_attempted_at"),
        "last_apply_at": run.get("last_apply_at"),
        "last_verification_at": run.get("last_verification_at"),
        "checkpoint_and_rollback": run.get("checkpoint_and_rollback"),
    }


def _entry_summary(entry: Mapping[str, Any], *, include_details: bool) -> dict[str, Any]:
    out = {
        "candidate": entry.get("key"),
        "object_type": entry.get("candidate", {}).get("object_type"),
        "identifier": entry.get("candidate", {}).get("identifier"),
        "dependencies": entry.get("candidate", {}).get("dependencies", []),
        "status": entry.get("status"),
        "retryable": entry.get("retryable", False),
        "attempts": entry.get("attempts", 0),
        "requires_secret_input": entry.get("requires_secret_input", False),
        "required_secret_names": entry.get("required_secret_names", []),
        "last_error": entry.get("last_error"),
        "dry_run_ok": entry.get("dry_run_ok", False),
        "verification": entry.get("verification"),
    }
    if include_details:
        out["source_candidate"] = entry.get("candidate")
        out["last_result"] = entry.get("last_result")
        out["attempt_history"] = entry.get("attempt_history", [])
    return out


class AOS8MigrationOrchestrator:
    """Create, apply, resume, and verify bounded AOS8 migration runs."""

    def __init__(
        self,
        store: MigrationRunStore,
        adapter_factory: AdapterFactory,
    ) -> None:
        self.store = store
        self.adapter_factory = adapter_factory

    def _adapter(
        self,
        target: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
        *,
        secret_inputs: Mapping[str, Mapping[str, str]] | None = None,
        placeholders: bool = False,
    ) -> BaseCentralTargetAdapter:
        secrets = (
            _placeholder_secret_inputs(candidates)
            if placeholders
            else dict(secret_inputs or {})
        )
        return self.adapter_factory(_target_context(target, secret_inputs=secrets))

    def preview(
        self,
        candidates: Iterable[Mapping[str, Any]],
        target: Mapping[str, Any],
        *,
        selected: Iterable[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_candidates = self._validate_candidates(candidates)
        selected_set = set(selected) if selected is not None else None
        adapter = self._adapter(target, safe_candidates, placeholders=True)
        preview = adapter.preview(safe_candidates, selected=selected_set)
        operations = preview.get("operations", [])
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        preview["operations"] = operations[
            bounded_offset : bounded_offset + bounded_limit
        ]
        preview["pagination"] = {
            "offset": bounded_offset,
            "limit": bounded_limit,
            "total": len(operations),
            "truncated": bounded_offset + bounded_limit < len(operations),
        }
        preview["candidate_count"] = len(operations)
        preview["secrets_persisted"] = False
        return _sanitize(preview)

    def create_run(
        self,
        candidates: Iterable[Mapping[str, Any]],
        target: Mapping[str, Any],
        *,
        selected: Iterable[str] | None = None,
        run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_candidates = self._validate_candidates(candidates)
        selected_set = set(selected) if selected is not None else None
        adapter = self._adapter(target, safe_candidates, placeholders=True)
        full_preview = adapter.preview(safe_candidates, selected=selected_set)
        operation_by_key = {
            str(operation["candidate"]): operation
            for operation in full_preview.get("operations", [])
        }
        candidate_by_key = {
            _candidate_key(candidate): candidate for candidate in safe_candidates
        }
        selected_candidates = [
            candidate_by_key[str(operation["candidate"])]
            for operation in full_preview.get("operations", [])
        ]
        fingerprint = _run_fingerprint(
            selected_candidates,
            full_preview["target"],
            operation_by_key,
        )
        resolved_run_id = validate_run_id(
            run_id or f"aos8-{fingerprint[:16]}"
        )
        path = self.store.path_for(resolved_run_id)
        if path.exists():
            existing = self.store.load(resolved_run_id)
            if existing.get("fingerprint") != fingerprint:
                raise MigrationRunError(
                    f"Migration run {resolved_run_id!r} already exists with different input."
                )
            return self.get_run(
                resolved_run_id,
                limit=limit,
                offset=offset,
                include_details=False,
            )

        entries: list[dict[str, Any]] = []
        for candidate in selected_candidates:
            key = _candidate_key(candidate)
            operation = operation_by_key[key]
            initial_status = str(operation.get("status", "pending"))
            if initial_status == "ready":
                initial_status = "pending"
            retryable = initial_status in {"pending", "blocked", "failed"}
            errors = [
                *operation.get("unsupported_warnings", []),
                *operation.get("blockers", []),
            ]
            entries.append(
                {
                    "key": key,
                    "candidate": candidate,
                    "status": initial_status,
                    "retryable": retryable,
                    "attempts": 0,
                    "requires_secret_input": bool(
                        candidate.get("requires_secret_input")
                    ),
                    "required_secret_names": _required_secret_names(candidate),
                    "dry_run_ok": initial_status == "skipped",
                    "last_error": "; ".join(errors) if errors else None,
                    "last_result": None,
                    "attempt_history": [],
                    "verification": None,
                }
            )
        created_at = _now()
        run: dict[str, Any] = {
            "schema_version": 1,
            "run_id": resolved_run_id,
            "fingerprint": fingerprint,
            "status": "pending",
            "created_at": created_at,
            "updated_at": created_at,
            "target": full_preview["target"],
            "checkpoint_and_rollback": full_preview["checkpoint_and_rollback"],
            "dry_run_attempted_at": None,
            "last_apply_at": None,
            "last_verification_at": None,
            "candidates": entries,
        }
        _refresh_run_status(run)
        self.store.save(run)
        return self.get_run(
            resolved_run_id,
            limit=limit,
            offset=offset,
            include_details=False,
        )

    def get_run(
        self,
        run_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        run = self.store.load(run_id)
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        entries = run["candidates"]
        return {
            **_run_summary(run),
            "fingerprint": run.get("fingerprint"),
            "secrets_persisted": False,
            "candidates": [
                _entry_summary(entry, include_details=include_details)
                for entry in entries[
                    bounded_offset : bounded_offset + bounded_limit
                ]
            ],
            "pagination": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": len(entries),
                "truncated": bounded_offset + bounded_limit < len(entries),
            },
        }

    def list_runs(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self.store.list_runs(limit=limit, offset=offset)

    def apply(
        self,
        run_id: str,
        *,
        dry_run: bool,
        confirmation: bool,
        target_secrets: Mapping[str, Mapping[str, str]] | None = None,
        retry_failed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.store.lock_run(run_id):
            return self._apply_locked(
                run_id,
                dry_run=dry_run,
                confirmation=confirmation,
                target_secrets=target_secrets,
                retry_failed=retry_failed,
                limit=limit,
                offset=offset,
            )

    def _apply_locked(
        self,
        run_id: str,
        *,
        dry_run: bool,
        confirmation: bool,
        target_secrets: Mapping[str, Mapping[str, str]] | None,
        retry_failed: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        run = self.store.load(run_id)
        supplied_secrets = dict(target_secrets or {})
        secret_values = tuple(
            value
            for bundle in supplied_secrets.values()
            for value in bundle.values()
            if isinstance(value, str) and value
        )
        if not dry_run and not confirmation:
            raise WriteGateError(
                "Real migration apply requires confirmation=True."
            )
        if not dry_run and not run.get("dry_run_attempted_at"):
            raise WriteGateError(
                "Run aos8_apply_migration_run with dry_run=True before real writes."
            )

        candidates = [entry["candidate"] for entry in run["candidates"]]
        adapter = self._adapter(
            run["target"],
            candidates,
            secret_inputs=supplied_secrets,
        )
        by_key = {entry["key"]: entry for entry in run["candidates"]}
        attempted_keys: list[str] = []
        for entry in run["candidates"]:
            key = str(entry["key"])
            status = str(entry.get("status", "pending"))
            if status in _TERMINAL or status == "applied":
                continue
            if status == "failed" and not retry_failed:
                continue
            if status == "blocked" and not entry.get("retryable", False):
                continue
            if status not in {"pending", "blocked", "failed"}:
                continue

            attempted_keys.append(key)
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            if entry.get("requires_secret_input"):
                missing = [
                    name
                    for name in entry.get("required_secret_names", [])
                    if not isinstance(supplied_secrets.get(key, {}).get(name), str)
                    or not supplied_secrets[key][name].strip()
                ]
                if missing:
                    self._record_entry(
                        run,
                        entry,
                        mode="dry-run" if dry_run else "apply",
                        status="blocked",
                        error=(
                            "Caller must supply target secrets again for this "
                            f"attempt: {missing}"
                        ),
                        result=None,
                        retryable=True,
                        secret_values=secret_values,
                    )
                    continue

            inline_dependencies = adapter.candidate_action(
                entry["candidate"]
            ).inline_dependencies
            dependency_success = {
                dependency
                for dependency in entry["candidate"].get("dependencies", [])
                if dependency in inline_dependencies
                or (
                    dependency in by_key
                    and (
                        by_key[dependency].get("status") in _TERMINAL_SUCCESS
                        if not dry_run
                        else bool(by_key[dependency].get("dry_run_ok"))
                    )
                )
            }
            dependency_failures = [
                dependency
                for dependency in entry["candidate"].get("dependencies", [])
                if dependency not in dependency_success
            ]
            if dependency_failures:
                self._record_entry(
                    run,
                    entry,
                    mode="dry-run" if dry_run else "apply",
                    status="blocked",
                    error=(
                        "Dependencies have not completed successfully: "
                        f"{sorted(dependency_failures)}"
                    ),
                    result=None,
                    retryable=True,
                    secret_values=secret_values,
                )
                continue
            if not dry_run and not entry.get("dry_run_ok"):
                self._record_entry(
                    run,
                    entry,
                    mode="apply",
                    status="blocked",
                    error="A successful dry-run is required before applying this candidate.",
                    result=None,
                    retryable=True,
                    secret_values=secret_values,
                )
                continue

            options = {
                "selected": {key},
                "include_dependency_closure": False,
                "allow_unresolved_blockers": True,
                "satisfied_dependencies": dependency_success,
            }
            try:
                result = (
                    adapter.dry_run(candidates, **options)
                    if dry_run
                    else adapter.execute(
                        candidates,
                        dry_run=False,
                        confirmation=True,
                        **options,
                    )
                )
                candidate_result = next(
                    (
                        item
                        for item in result.get("results", [])
                        if item.get("candidate") == key
                    ),
                    {
                        "candidate": key,
                        "status": "failed",
                        "errors": ["Adapter returned no candidate result."],
                        "results": [],
                    },
                )
                result_status = str(candidate_result.get("status", "failed"))
                if dry_run and result_status == "dry-run":
                    entry["dry_run_ok"] = True
                    persisted_status = "pending"
                    retryable = True
                else:
                    persisted_status = result_status
                    retryable = result_status in {"failed", "blocked"}
                error = "; ".join(
                    str(item) for item in candidate_result.get("errors", []) if item
                ) or None
                self._record_entry(
                    run,
                    entry,
                    mode="dry-run" if dry_run else "apply",
                    status=persisted_status,
                    error=error,
                    result=candidate_result,
                    retryable=retryable,
                    secret_values=secret_values,
                )
            except Exception as exc:
                self._record_entry(
                    run,
                    entry,
                    mode="dry-run" if dry_run else "apply",
                    status="failed",
                    error=str(exc),
                    result=None,
                    retryable=True,
                    secret_values=secret_values,
                )

        if dry_run:
            run["dry_run_attempted_at"] = _now()
        else:
            run["last_apply_at"] = _now()
        _refresh_run_status(run)
        self.store.save(run)
        response = self.get_run(
            run_id,
            limit=limit,
            offset=offset,
            include_details=True,
        )
        response["dry_run"] = dry_run
        response["attempted_candidates"] = attempted_keys[:MAX_RESULT_ITEMS]
        response["retry_failed"] = retry_failed
        return _sanitize(response, secret_values=secret_values)

    def _record_entry(
        self,
        run: dict[str, Any],
        entry: dict[str, Any],
        *,
        mode: str,
        status: str,
        error: str | None,
        result: Any,
        retryable: bool,
        secret_values: Iterable[str],
    ) -> None:
        safe_error = _sanitize(error, secret_values=secret_values) if error else None
        safe_result = _sanitize(result, secret_values=secret_values)
        entry["status"] = status
        entry["retryable"] = retryable
        entry["last_error"] = safe_error
        entry["last_result"] = safe_result
        history = list(entry.get("attempt_history", []))
        history.append(
            {
                "at": _now(),
                "mode": mode,
                "status": status,
                "error": safe_error,
                "result": safe_result,
            }
        )
        entry["attempt_history"] = history[-MAX_HISTORY_ITEMS:]
        _refresh_run_status(run)
        self.store.save(run)

    def verify(
        self,
        run_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.store.lock_run(run_id):
            return self._verify_locked(run_id, limit=limit, offset=offset)

    def _verify_locked(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        run = self.store.load(run_id)
        candidates = [entry["candidate"] for entry in run["candidates"]]
        adapter = self._adapter(run["target"], candidates, placeholders=True)
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        selected_entries = run["candidates"][
            bounded_offset : bounded_offset + bounded_limit
        ]
        comparisons: list[dict[str, Any]] = []
        for entry in selected_entries:
            verification = self._verify_entry(adapter, entry)
            entry["verification"] = verification
            comparisons.append(verification)
            _refresh_run_status(run)
            self.store.save(run)
        run["last_verification_at"] = _now()
        _refresh_run_status(run)
        self.store.save(run)
        return {
            "run_id": run_id,
            "read_only": True,
            "verification_scope": (
                "Identity presence plus directly comparable returned fields only; "
                "this does not claim full semantic equivalence."
            ),
            "comparisons": comparisons,
            "pagination": {
                "offset": bounded_offset,
                "limit": bounded_limit,
                "total": len(run["candidates"]),
                "truncated": bounded_offset + bounded_limit < len(run["candidates"]),
            },
            "checkpoint_and_rollback": run["checkpoint_and_rollback"],
        }

    def _verify_entry(
        self,
        adapter: BaseCentralTargetAdapter,
        entry: Mapping[str, Any],
    ) -> dict[str, Any]:
        key = str(entry["key"])
        status = str(entry.get("status"))
        source = _sanitize(entry.get("candidate"))
        base = {
            "candidate": key,
            "apply_status": status,
            "source_candidate_intent": source,
            "apply_result": _sanitize(entry.get("last_result")),
        }
        if status not in _TERMINAL_SUCCESS:
            return {
                **base,
                "verification_status": "unverifiable",
                "reason": (
                    "Candidate is unsupported and remains unapplied."
                    if status == "unsupported"
                    else f"Candidate is not successfully applied/skipped (status={status})."
                ),
                "target_state": None,
                "field_comparison": [],
            }
        action = adapter.candidate_action(entry["candidate"])
        if action.read_operation is None:
            return {
                **base,
                "verification_status": "unverifiable",
                "reason": "No verified read operation exists for this mapping.",
                "target_state": None,
                "field_comparison": [],
            }
        try:
            target_state = adapter.read_invoker(action.read_operation)
        except Exception as exc:
            return {
                **base,
                "verification_status": "unverifiable",
                "reason": f"Target verification read failed: {exc}",
                "target_state": None,
                "field_comparison": [],
            }
        safe_target = _sanitize(target_state)
        identifier = action.read_operation.match_identifier or str(
            entry["candidate"].get("identifier")
        )
        if not _contains_identifier(safe_target, identifier):
            return {
                **base,
                "verification_status": "mismatch",
                "reason": "Target verification did not find the candidate identity.",
                "target_state": safe_target,
                "field_comparison": [],
            }
        expected = _expected_fields(action, entry["candidate"])
        target_fields = _flatten_fields(safe_target)
        comparisons: list[dict[str, Any]] = []
        mismatches: list[str] = []
        for field, expected_value in expected.items():
            aliases = _field_aliases(field)
            matches = [
                target_fields[alias]
                for alias in aliases
                if alias in target_fields
            ]
            if not matches:
                continue
            matched = any(_comparable_equal(expected_value, actual) for actual in matches)
            comparisons.append(
                {
                    "field": field,
                    "expected": expected_value,
                    "actual": matches[0],
                    "status": "match" if matched else "mismatch",
                }
            )
            if not matched:
                mismatches.append(field)
        return {
            **base,
            "verification_status": "mismatch" if mismatches else "verified",
            "reason": (
                f"Directly comparable fields differed: {sorted(mismatches)}"
                if mismatches
                else (
                    "Candidate identity was present; directly comparable returned "
                    "fields matched. Unreturned fields were not asserted."
                )
            ),
            "target_state": safe_target,
            "field_comparison": comparisons,
        }

    @staticmethod
    def _validate_candidates(
        candidates: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        values = list(candidates)
        if not values:
            raise MigrationRunError("At least one migration candidate is required.")
        if len(values) > MAX_CANDIDATES:
            raise MigrationRunError(
                f"Migration runs are limited to {MAX_CANDIDATES} candidates."
            )
        safe = [_safe_candidate(candidate) for candidate in values]
        keys = [_candidate_key(candidate) for candidate in safe]
        if len(set(keys)) != len(keys):
            raise MigrationRunError("Migration candidate keys must be unique.")
        return safe


def _contains_identifier(value: Any, identifier: str) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(_contains_identifier(item, identifier) for item in value)
    if isinstance(value, Mapping):
        if value.get("found") is False:
            return False
        error = str(value.get("error", ""))
        if "404" in error or "not found" in error.lower():
            return False
        identity_fields = (
            "name",
            "ssid",
            "vlan",
            "vlan_id",
            "vlan-id",
            "profile-name",
            "id",
        )
        if any(str(value.get(field)) == identifier for field in identity_fields):
            return True
        return any(_contains_identifier(item, identifier) for item in value.values())
    return False


def _flatten_fields(value: Any, out: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = out if out is not None else {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if not isinstance(item, (Mapping, list, tuple)):
                fields.setdefault(normalized, item)
            _flatten_fields(item, fields)
    elif isinstance(value, (list, tuple)):
        for item in value[:MAX_RESULT_ITEMS]:
            _flatten_fields(item, fields)
    return fields


def _expected_fields(action: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {"identifier": candidate.get("identifier")}
    if action.operations:
        for key, value in action.operations[0].arguments.items():
            normalized = _normalized_key(key)
            if normalized in {
                "dry_run",
                "scope_id",
                "persona",
                "cluster_scope_id",
                "cluster_name",
                "gateway_scope_id",
                "gateway_name",
            } or _is_sensitive_key(normalized):
                continue
            if value not in (None, "", [], {}):
                expected[normalized] = _sanitize(value)
    return expected


def _field_aliases(field: str) -> set[str]:
    aliases = {field}
    if field == "identifier":
        aliases.update({"name", "ssid", "vlan", "vlan_id", "id", "profile_name"})
    if field in {"vlan_name", "ssid_name"}:
        aliases.add("name")
    if field == "auth_server_address":
        aliases.update({"auth_server_address", "address", "host"})
    return aliases


def _comparable_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        return str(expected) == str(actual)
    if isinstance(expected, list):
        return [str(item) for item in expected] == (
            [str(item) for item in actual] if isinstance(actual, list) else [str(actual)]
        )
    return str(expected) == str(actual)
