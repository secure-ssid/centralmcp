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
# Bounds for the explicit, non-secret operator-context maps
# (`external_object_references`, `ap_group_target_map`,
# `ap_group_device_serials`) accepted at the MCP/orchestrator boundary --
# these are operator-declared reference data (an already-existing Classic
# auth-server name; an AP-group -> Classic-group mapping; device serials),
# never secrets, but still caller-controlled input that must be bounded
# before it is ever used to build a `TargetContext`. They are runtime-only,
# exactly like `secret_inputs`: bounded/validated here, then never written
# into persisted run state (see `_strip_operator_context`,
# `_operator_context_metadata`, `_reconcile_operator_context`).
MAX_OPERATOR_CONTEXT_ENTRIES = 100
MAX_OPERATOR_CONTEXT_STRING_LENGTH = 256
MAX_AP_GROUP_SERIALS_PER_GROUP = 64
MAX_SERIAL_STRING_LENGTH = 64
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
# 0.5: there is no rollback execution path, so "rolled_back" is not a
# reachable candidate status (see AOS8MigrationOrchestrator's module
# docstring / docs/aos8-migration-contract-matrix.md §2.1/§5).
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
        # Type-aware: LDAP's New Central secret is the flat `admin-password`
        # bind-password field (`admin_password`); RADIUS/TACACS both use the
        # nested `shared-secret-config` object (`shared_secret`). See
        # pipeline/aos8_target_adapters.py `_map_auth_server`/`_auth_server_body`.
        server_type = str((candidate.get("payload") or {}).get("server_type") or "").lower()
        if server_type == "ldap":
            return ["admin_password"]
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


def _bounded_operator_string(
    value: Any,
    field_name: str,
    *,
    max_length: int = MAX_OPERATOR_CONTEXT_STRING_LENGTH,
) -> str:
    if not isinstance(value, str):
        raise MigrationRunError(f"{field_name} must be a string.")
    text = value.strip()
    if not text:
        raise MigrationRunError(f"{field_name} must be a non-empty string.")
    if len(value) > max_length:
        raise MigrationRunError(f"{field_name} exceeds {max_length} characters.")
    return value


# `external_object_references`, `ap_group_target_map`, and
# `ap_group_device_serials` carry operator-declared *reference* strings (an
# existing object's name, an AP-group/Classic-group name, a device serial).
# Their names and values are arbitrary caller-chosen identifiers -- e.g. a
# Classic auth-server profile literally named "Token-Group", or an AP group
# named "private-key-infra" -- so they must never be screened with
# secret-keyword/secret-shaped-content heuristics (`_is_sensitive_key`, or a
# former `_looks_like_credential_material`): those heuristics are only sound
# against dictionary field names with a known, fixed schema (e.g. a
# candidate payload's "shared_secret" field), not against free-form operator
# identifiers. Structural bounds (type, non-empty, length, count) are the
# only validation applied here. The actual secret-persistence risk is
# eliminated by treating these three maps as runtime-only operator input,
# exactly like `secret_inputs`: never written into persisted run state,
# history, fingerprints, candidate snapshots, or returned by a later
# get/list call (see `_strip_operator_context`, `_operator_context_metadata`,
# and `_operator_context_values` below).


def _validate_external_object_references(
    value: Any,
) -> dict[str, dict[str, str]]:
    """Bound and validate the explicit, non-secret object-reference map
    (e.g. an already-existing Classic auth-server name for a conditional
    WPA3-Enterprise WLAN). Backward compatible with persisted 0.4 target
    dictionaries, which never had this key: absent/empty input returns {}.
    """
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise MigrationRunError("external_object_references must be an object.")
    if len(value) > MAX_OPERATOR_CONTEXT_ENTRIES:
        raise MigrationRunError(
            "external_object_references may not exceed "
            f"{MAX_OPERATOR_CONTEXT_ENTRIES} candidate keys."
        )
    bounded: dict[str, dict[str, str]] = {}
    for candidate_key, refs in value.items():
        key_str = _bounded_operator_string(
            candidate_key, "external_object_references key"
        )
        if not isinstance(refs, Mapping):
            raise MigrationRunError(
                f"external_object_references[{key_str!r}] must be an object "
                "of reference name -> value."
            )
        if len(refs) > MAX_OPERATOR_CONTEXT_ENTRIES:
            raise MigrationRunError(
                f"external_object_references[{key_str!r}] may not exceed "
                f"{MAX_OPERATOR_CONTEXT_ENTRIES} entries."
            )
        bounded_refs: dict[str, str] = {}
        for ref_name, ref_value in refs.items():
            ref_name_str = _bounded_operator_string(
                ref_name, "external_object_references reference name"
            )
            bounded_value = _bounded_operator_string(
                ref_value,
                f"external_object_references[{key_str!r}][{ref_name_str!r}]",
            )
            bounded_refs[ref_name_str] = bounded_value
        bounded[key_str] = bounded_refs
    return bounded


def _validate_ap_group_target_map(value: Any) -> dict[str, str]:
    """Bound and validate the explicit, operator-provided AOS8 ap_group name
    -> Classic Central group name mapping. Backward compatible with
    persisted 0.4 target dictionaries: absent/empty input returns {}.
    """
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise MigrationRunError("ap_group_target_map must be an object.")
    if len(value) > MAX_OPERATOR_CONTEXT_ENTRIES:
        raise MigrationRunError(
            f"ap_group_target_map may not exceed {MAX_OPERATOR_CONTEXT_ENTRIES} entries."
        )
    bounded: dict[str, str] = {}
    for ap_group, classic_group in value.items():
        ap_group_str = _bounded_operator_string(ap_group, "ap_group_target_map key")
        bounded_value = _bounded_operator_string(
            classic_group, f"ap_group_target_map[{ap_group_str!r}]"
        )
        bounded[ap_group_str] = bounded_value
    return bounded


def _validate_ap_group_device_serials(value: Any) -> dict[str, tuple[str, ...]]:
    """Bound and validate the explicit, operator-provided AOS8 ap_group name
    -> device serial numbers mapping. Backward compatible with persisted 0.4
    target dictionaries: absent/empty input returns {}.
    """
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise MigrationRunError("ap_group_device_serials must be an object.")
    if len(value) > MAX_OPERATOR_CONTEXT_ENTRIES:
        raise MigrationRunError(
            "ap_group_device_serials may not exceed "
            f"{MAX_OPERATOR_CONTEXT_ENTRIES} entries."
        )
    bounded: dict[str, tuple[str, ...]] = {}
    for ap_group, serials in value.items():
        ap_group_str = _bounded_operator_string(ap_group, "ap_group_device_serials key")
        if not isinstance(serials, (list, tuple)):
            raise MigrationRunError(
                f"ap_group_device_serials[{ap_group_str!r}] must be a list "
                "of serial number strings."
            )
        if len(serials) > MAX_AP_GROUP_SERIALS_PER_GROUP:
            raise MigrationRunError(
                f"ap_group_device_serials[{ap_group_str!r}] may not exceed "
                f"{MAX_AP_GROUP_SERIALS_PER_GROUP} serial numbers."
            )
        bounded_serials = []
        for serial in serials:
            bounded_serial = _bounded_operator_string(
                serial,
                f"ap_group_device_serials[{ap_group_str!r}] entry",
                max_length=MAX_SERIAL_STRING_LENGTH,
            )
            bounded_serials.append(bounded_serial)
        bounded[ap_group_str] = tuple(bounded_serials)
    return bounded


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
            external_object_references=_validate_external_object_references(
                target.get("external_object_references")
            ),
            ap_group_target_map=_validate_ap_group_target_map(
                target.get("ap_group_target_map")
            ),
            ap_group_device_serials=_validate_ap_group_device_serials(
                target.get("ap_group_device_serials")
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MigrationRunError(f"Invalid persisted target context: {exc}") from exc


# `external_object_references`, `ap_group_target_map`, and
# `ap_group_device_serials` are runtime-only operator input -- exactly like
# `secret_inputs` -- and must never be written into persisted run state,
# fingerprints, history, candidate snapshots, action previews saved to
# disk, list/get output, checkpoints, or logs. The helpers below give the
# orchestrator a way to (a) prove, without storing any of the actual
# values, that a later `apply()` call resupplied the *same* bounded input
# used at `create_run()` time (non-reversible SHA-256 fingerprint + count
# metadata only), and (b) strip those values out of any adapter-produced
# text (errors/results) before it is ever persisted.
_OPERATOR_CONTEXT_FIELDS = (
    "external_object_references",
    "ap_group_target_map",
    "ap_group_device_serials",
)


def _operator_context_fingerprint(value: Any) -> dict[str, Any]:
    """Non-reversible count + SHA-256 fingerprint for one bounded operator-
    context map. Safe to persist: it reveals neither the reference names,
    group names, nor device serials that produced it -- only that some
    bounded input was supplied, and a hash a caller can use to prove a later
    resupply is the exact same input (see `_reconcile_operator_context`).
    """
    if not value:
        return {"count": 0, "fingerprint": None}
    return {
        "count": len(value),
        "fingerprint": hashlib.sha256(_canonical_json(value).encode()).hexdigest(),
    }


def _operator_context_metadata(
    external_object_references: Mapping[str, Mapping[str, str]],
    ap_group_target_map: Mapping[str, str],
    ap_group_device_serials: Mapping[str, tuple[str, ...]],
) -> dict[str, dict[str, Any]]:
    return {
        "external_object_references": _operator_context_fingerprint(
            external_object_references
        ),
        "ap_group_target_map": _operator_context_fingerprint(ap_group_target_map),
        "ap_group_device_serials": _operator_context_fingerprint(
            {key: list(value) for key, value in ap_group_device_serials.items()}
        ),
    }


def _operator_context_values(
    external_object_references: Mapping[str, Mapping[str, str]],
    ap_group_target_map: Mapping[str, str],
    ap_group_device_serials: Mapping[str, Iterable[str]],
) -> tuple[str, ...]:
    """Every literal string value carried by the three runtime-only
    operator-context maps. Used purely as an exact-match redaction set
    (never a content heuristic) so that if an adapter ever echoes one of
    these values back into an operation payload, error, or result, it is
    scrubbed out -- the same mechanism already used for `secret_inputs` --
    before that text is persisted to run state.
    """
    values: list[str] = []
    for refs in external_object_references.values():
        values.extend(str(item) for item in refs.values())
    values.extend(str(item) for item in ap_group_target_map.values())
    for serials in ap_group_device_serials.values():
        values.extend(str(item) for item in serials)
    return tuple(item for item in values if item)


def _strip_operator_context(target: Mapping[str, Any]) -> dict[str, Any]:
    """Remove the three runtime-only operator-context maps from a target
    dict before it is ever written to persisted run state or returned via a
    later get/list call. They are supplied fresh on every call that needs
    them (`preview`/`create_run`/`apply`) and are never retrievable from a
    completed run afterward.
    """
    return {
        key: value for key, value in target.items() if key not in _OPERATOR_CONTEXT_FIELDS
    }


def _run_fingerprint(
    candidates: list[dict[str, Any]],
    target: Mapping[str, Any],
    selected: Iterable[str] | None,
    operator_context_metadata: Mapping[str, Any],
) -> str:
    material = {
        "candidates": candidates,
        "target": dict(target),
        "selected": sorted(selected or ()),
        "operator_context_metadata": operator_context_metadata,
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
        # Defense in depth: `external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` are runtime-only
        # operator input and current code never writes them into a run's
        # persisted `target`. But a state file written by a prior,
        # vulnerable revision (or hand-edited) could still carry them --
        # sanitize on every read so a stale on-disk value is never served
        # back through `get_run`/`list_runs`/`apply`/`verify`. The next
        # `save()` for this run naturally persists the sanitized version.
        target = value.get("target")
        if isinstance(target, dict) and any(
            field in target for field in _OPERATOR_CONTEXT_FIELDS
        ):
            value["target"] = _strip_operator_context(target)
        return value

    def save(self, run: Mapping[str, Any]) -> None:
        run_id = validate_run_id(str(run.get("run_id", "")))
        # Defense in depth, mirroring `load()`: never let
        # `external_object_references`/`ap_group_target_map`/
        # `ap_group_device_serials` reach disk even if some future caller
        # forgets to strip them from `run["target"]` first.
        target = run.get("target")
        if isinstance(target, dict) and any(
            field in target for field in _OPERATOR_CONTEXT_FIELDS
        ):
            run = {**run, "target": _strip_operator_context(target)}
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
            "completed_with_issues" if "unsupported" in statuses else "completed"
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
        # Non-reversible count + SHA-256 metadata only -- never the actual
        # `external_object_references`/`ap_group_target_map`/
        # `ap_group_device_serials` values, which are runtime-only operator
        # input and are never persisted (see `_operator_context_metadata`).
        # A caller that needs to resupply these on `apply()` can use this to
        # confirm which fields (if any) the run was created with.
        "operator_context_metadata": run.get("operator_context_metadata", {}),
        "candidate_count": len(run.get("candidates", [])),
        "status_counts": _status_counts(run),
        "dry_run_attempted_at": run.get("dry_run_attempted_at"),
        "last_apply_at": run.get("last_apply_at"),
        "last_verification_at": run.get("last_verification_at"),
        # 0.5: no rollback execution path exists; `checkpoint_and_rollback`
        # below is the pre-existing, unrelated New Central/Classic Central
        # device checkpoint guidance (see BaseCentralTargetAdapter.
        # checkpoint_guidance), not a claim about this orchestrator's own
        # (nonexistent) rollback capability.
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
        # `preview()` is stateless -- nothing it returns is written to disk
        # -- so it may echo `external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` back in this one
        # response for operator review (same "show the payload before you
        # commit" rule as everything else in `preview`). They are still
        # runtime-only: this flag documents that they are never persisted.
        preview["operator_context_persisted"] = False
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
        # `external_object_references`/`ap_group_target_map`/
        # `ap_group_device_serials` are runtime-only operator input, just
        # like `secret_inputs`: used transiently above to map this run's
        # candidates, but never persisted. `operator_context_metadata` is a
        # non-reversible count + SHA-256 fingerprint per field, safe to
        # store, that lets a later `apply()` call prove it is resupplying
        # the exact same input (see `_reconcile_operator_context`).
        # `operator_context_values` is the exact-match redaction set used to
        # scrub those values out of any adapter-produced text below.
        operator_context_values = _operator_context_values(
            adapter.context.external_object_references,
            adapter.context.ap_group_target_map,
            adapter.context.ap_group_device_serials,
        )
        operator_context_metadata = _operator_context_metadata(
            adapter.context.external_object_references,
            adapter.context.ap_group_target_map,
            adapter.context.ap_group_device_serials,
        )
        persisted_target = _strip_operator_context(full_preview["target"])
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
            persisted_target,
            operation_by_key,
            operator_context_metadata,
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
            joined_errors = "; ".join(errors) if errors else None
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
                    "last_error": (
                        _sanitize(joined_errors, secret_values=operator_context_values)
                        if joined_errors
                        else None
                    ),
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
            "target": persisted_target,
            "operator_context_metadata": operator_context_metadata,
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
        external_object_references: Mapping[str, Mapping[str, str]] | None = None,
        ap_group_target_map: Mapping[str, str] | None = None,
        ap_group_device_serials: Mapping[str, Iterable[str]] | None = None,
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
                external_object_references=external_object_references,
                ap_group_target_map=ap_group_target_map,
                ap_group_device_serials=ap_group_device_serials,
                retry_failed=retry_failed,
                limit=limit,
                offset=offset,
            )

    def _reconcile_operator_context(
        self,
        run: dict[str, Any],
        *,
        external_object_references: Mapping[str, Mapping[str, str]] | None,
        ap_group_target_map: Mapping[str, str] | None,
        ap_group_device_serials: Mapping[str, Iterable[str]] | None,
    ) -> dict[str, Any]:
        """Reconcile this apply() call's runtime-only operator-context
        resupply against the non-reversible fingerprint/count metadata
        recorded when the run was created, without ever storing the actual
        values. Returns a `target`-shaped dict (never persisted by the
        caller) suitable for building an adapter for this call only.

        - If the run was created with a given field (recorded count > 0)
          and this call does not resupply it, that is a bounded-resupply
          failure -- raised explicitly, never silently treated as absent.
        - If resupplied, the fingerprint of the validated resupply must
          match the fingerprint recorded at create time, or the call is
          rejected (the caller is not supplying the same input the run's
          candidates were mapped against).
        - If the run was created without a given field (recorded count 0,
          or no metadata at all -- e.g. a pre-fix/0.4 run), a fresh resupply
          is accepted and the run's metadata is updated to match, so later
          calls must stay consistent with it in turn.
        """
        supplied = {
            "external_object_references": external_object_references,
            "ap_group_target_map": ap_group_target_map,
            "ap_group_device_serials": ap_group_device_serials,
        }
        validators: dict[str, Callable[[Any], Any]] = {
            "external_object_references": _validate_external_object_references,
            "ap_group_target_map": _validate_ap_group_target_map,
            "ap_group_device_serials": _validate_ap_group_device_serials,
        }
        stored_metadata = run.get("operator_context_metadata") or {}
        effective: dict[str, Any] = {}
        new_metadata: dict[str, Any] = {}
        for field in _OPERATOR_CONTEXT_FIELDS:
            validated = validators[field](supplied[field])
            recorded = stored_metadata.get(field) or {"count": 0, "fingerprint": None}
            if recorded.get("count", 0) > 0:
                if supplied[field] is None:
                    raise MigrationRunError(
                        f"This run was created with {field!r} operator context; "
                        "it must be resupplied on every apply() call (it is "
                        "never persisted) -- pass the exact same value again."
                    )
                fingerprint = _operator_context_fingerprint(validated)
                if fingerprint.get("fingerprint") != recorded.get("fingerprint"):
                    raise MigrationRunError(
                        f"Resupplied {field!r} does not match the value used "
                        "when this run was created."
                    )
                new_metadata[field] = recorded
            else:
                new_metadata[field] = _operator_context_fingerprint(validated)
            effective[field] = validated
        run["operator_context_metadata"] = new_metadata
        return {**run["target"], **effective}

    def _apply_locked(
        self,
        run_id: str,
        *,
        dry_run: bool,
        confirmation: bool,
        target_secrets: Mapping[str, Mapping[str, str]] | None,
        external_object_references: Mapping[str, Mapping[str, str]] | None = None,
        ap_group_target_map: Mapping[str, str] | None = None,
        ap_group_device_serials: Mapping[str, Iterable[str]] | None = None,
        retry_failed: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        run = self.store.load(run_id)
        effective_target = self._reconcile_operator_context(
            run,
            external_object_references=external_object_references,
            ap_group_target_map=ap_group_target_map,
            ap_group_device_serials=ap_group_device_serials,
        )
        supplied_secrets = dict(target_secrets or {})
        secret_values = tuple(
            value
            for bundle in supplied_secrets.values()
            for value in bundle.values()
            if isinstance(value, str) and value
        ) + _operator_context_values(
            effective_target["external_object_references"],
            effective_target["ap_group_target_map"],
            effective_target["ap_group_device_serials"],
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
            effective_target,
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
        # No operator-context resupply is needed here: `verify()` only ever
        # inspects entries whose status is already a terminal success
        # (`applied`/`skipped`, see `_verify_entry`), and none of the
        # mappings that consume `external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` can ever reach
        # that state -- WPA3-Enterprise is unconditionally `dry_run_only`
        # (real execution always refused) and AP-group mappings never leave
        # `unsupported` (contract matrix §5/§6.11). `run["target"]` here
        # never carries those fields (see `_strip_operator_context`).
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
        expected, secret_fields = _expected_fields(action, entry["candidate"])
        target_fields = _flatten_fields(safe_target)
        comparisons: list[dict[str, Any]] = []
        mismatches: list[str] = []
        verified_fields: list[str] = []
        unverifiable_fields: list[str] = []
        for field, expected_value in expected.items():
            aliases = _field_aliases(field)
            matches = [
                target_fields[alias]
                for alias in aliases
                if alias in target_fields
            ]
            if not matches:
                # Explicitly reported, not silently skipped: the target read
                # simply did not return this field (e.g. it is write-only, or
                # the read shape differs from the write shape).
                comparisons.append(
                    {
                        "field": field,
                        "expected": expected_value,
                        "actual": None,
                        "status": "unverifiable",
                        "reason": "field was not present in the target read response",
                    }
                )
                unverifiable_fields.append(field)
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
            if matched:
                verified_fields.append(field)
            else:
                mismatches.append(field)
        for field in sorted(secret_fields):
            # Secrets are never returned by a GET -- report as unverifiable,
            # never as a mismatch (which would be a false negative for every
            # secret-bearing candidate).
            comparisons.append(
                {
                    "field": field,
                    "expected": "***",
                    "actual": None,
                    "status": "unverifiable",
                    "reason": "secret field is not returned by target reads and cannot be verified",
                }
            )
            unverifiable_fields.append(field)

        comparable_fields = [f for f in expected if f not in secret_fields]
        # "identifier" is the identity field already confirmed by the
        # `_contains_identifier` gate above; it must not, by itself, count
        # as a verified *payload* field for the purposes of this decision --
        # otherwise a candidate with real payload fields that are all
        # unverifiable would still be reported "verified" on identity alone.
        payload_fields = [f for f in comparable_fields if f != "identifier"]
        payload_verified = [f for f in verified_fields if f != "identifier"]
        # Finding #3: if ANY expected non-secret payload field is absent or
        # otherwise unverifiable against the target read, status must be
        # "partially_verified" -- never "verified" -- even when other
        # payload fields did match. Full "verified" now requires every
        # non-secret payload field to be individually confirmed.
        payload_unverifiable = [f for f in payload_fields if f in unverifiable_fields]
        if mismatches:
            verification_status = "mismatch"
            reason = f"Directly comparable fields differed: {sorted(mismatches)}"
        elif payload_unverifiable:
            verification_status = "partially_verified"
            reason = (
                "Candidate identity was present, but one or more non-secret "
                "payload fields could not be confirmed against the target "
                f"read response: {sorted(payload_unverifiable)}"
            )
        else:
            verification_status = "verified"
            reason = (
                "Candidate identity was present; directly comparable returned "
                "fields matched."
                + (
                    f" Unverifiable fields (not returned by the read, or secret "
                    f"and never returned): {sorted(unverifiable_fields)}."
                    if unverifiable_fields
                    else " Unreturned fields were not asserted."
                )
            )
        return {
            **base,
            "verification_status": verification_status,
            "reason": reason,
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


def _flatten_fields(
    value: Any, out: dict[str, Any] | None = None, *, prefix: str = ""
) -> dict[str, Any]:
    """Flatten a nested payload/response into a comparable dict of fields.

    Every scalar leaf is recorded under BOTH its bare (unqualified) key --
    preserving the original, backward-compatible first-seen-wins matching
    used for simple envelope wrappers like `{"items": [{...}]}` or
    `{"config-assignment": [{...}]}` where exactly one element is relevant
    -- AND its fully index-qualified path (e.g. `servers[0].server-name`,
    `servers[1].position`), with deterministic (source-order) indices.

    Finding #3: the qualified paths are what catch a reordered, truncated,
    or extended array that the bare-key form alone would silently mask
    (e.g. a `servers` array missing its second entry would still
    "bare-key match" on the first entry's fields even though a real
    element is missing) -- without regressing any existing bare-key-based
    comparison for object/response envelopes that only ever expose one
    real "item".
    """
    fields = out if out is not None else {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            qualified = f"{prefix}.{normalized}" if prefix else normalized
            if isinstance(item, (Mapping, list, tuple)):
                _flatten_fields(item, fields, prefix=qualified)
            else:
                fields.setdefault(normalized, item)
                fields.setdefault(qualified, item)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value[:MAX_RESULT_ITEMS]):
            qualified = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if isinstance(item, (Mapping, list, tuple)):
                _flatten_fields(item, fields, prefix=qualified)
            else:
                fields.setdefault(qualified, item)
    return fields


_VERIFICATION_IGNORED_KEYS = {
    "dry_run",
    "scope_id",
    "persona",
    "cluster_scope_id",
    "cluster_name",
    "gateway_scope_id",
    "gateway_name",
    # `invocation="endpoint"` Operation.arguments wrapper keys -- never a
    # verifiable target field in their own right. Only relevant when an
    # operation has no `.payload` and we fall back to raw `.arguments`
    # (tool-invocation operations); endpoint operations always have
    # `.payload` populated and never reach this fallback.
    "method",
    "endpoint",
    "data",
}


def _expected_fields(
    action: Any, candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    """Return (expected non-secret fields, secret field names) for `action`.

    Sourced from `Operation.payload` when the primary operation is an
    `invocation="endpoint"` write (the exact request body New Central will
    receive) -- never from `method`/`endpoint`/the wrapper `data` argument.
    Tool-invocation operations have no `.payload`; their top-level
    `.arguments` (minus admin/context keys) are used instead. Secret fields
    (matched by `Operation.sensitive_argument_fields` or `_is_sensitive_key`)
    are separated out and never compared -- GET responses omit secret
    material, so they are reported as unverifiable rather than mismatched.
    """
    # Use the qualified `match_identifier` (the short, unqualified name New
    # Central actually returns, e.g. "ldap1") rather than the raw candidate
    # identifier (e.g. "ldap:ldap1", qualified by auth-server type) --
    # otherwise this synthetic field would never match a real target read
    # even when the true object identity check above already succeeded.
    read_operation = getattr(action, "read_operation", None)
    qualified_identifier = (
        getattr(read_operation, "match_identifier", None)
        if read_operation is not None
        else None
    ) or candidate.get("identifier")
    raw: dict[str, Any] = {"identifier": qualified_identifier}
    secret_fields: set[str] = set()
    if action.operations:
        primary = action.operations[0]
        sensitive = {_normalized_key(field) for field in primary.sensitive_argument_fields}
        source = primary.payload if primary.payload is not None else primary.arguments
        for key, value in source.items():
            normalized = _normalized_key(key)
            if normalized in _VERIFICATION_IGNORED_KEYS:
                continue
            if normalized in sensitive or _is_sensitive_key(normalized):
                secret_fields.add(normalized)
                continue
            if value in (None, "", [], {}):
                continue
            raw[normalized] = value
    expected = {key: _sanitize(value) for key, value in _flatten_fields(raw).items()}
    return expected, secret_fields


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
