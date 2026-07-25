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
from urllib.parse import unquote, unquote_plus

from pipeline.aos8_target_adapters import (
    MAX_SECRET_LENGTH,
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
# before it is ever used to build a `TargetContext`.
#
# Fail-closed contract: these three maps are accepted only by the
# stateless `preview()` path, which persists nothing and may use them
# transiently to build the returned dry-run preview. Every persistent
# workflow (`create_run`, `MigrationRunStore.save`, `apply`, stored
# get/list/history/checkpoint output) rejects any non-empty map outright
# with a clear error -- see `_reject_persisted_operator_context` -- rather
# than storing the raw values, a hash/fingerprint, a count, or any other
# resupply metadata derived from them. There is no verifier for these
# free-form operator identifiers, so persisting even a hash would create
# an offline-guessing surface; not persisting anything at all removes that
# surface entirely.
MAX_OPERATOR_CONTEXT_ENTRIES = 100
MAX_OPERATOR_CONTEXT_STRING_LENGTH = 256
MAX_AP_GROUP_SERIALS_PER_GROUP = 64
MAX_SERIAL_STRING_LENGTH = 64
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# Anchored scheme (RFC 3986 `scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" /
# "." )`) followed immediately by "://" -- used by `_is_url_like` to
# require that a string *is* a standalone absolute URL leaf, never that
# it merely contains "://" somewhere inside arbitrary prose.
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
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
    structural_redaction_values: Iterable[str] = (),
    redact_marker: str = "******",
    structural_redact_marker: str = "******",
    max_depth: int = 8,
    _depth: int = 0,
    _compiled_secrets: dict[str, "_SecretMatcher"] | None = None,
) -> Any:
    """Sanitize `value` for return/persistence across two independent
    redaction channels:

    - `secret_values`: actual runtime secrets (credentials, passphrases,
      shared secrets a caller supplied for a real apply/write). A real
      secret must never survive in any form it might appear in, so each
      secret is matched directly against the text with a single linear,
      regex-free scan (`_compile_secret_pattern` / `_SecretMatcher`) that
      treats every character of the secret as *either* its raw literal
      form, *or* its UTF-8
      percent-encoded byte sequence with every hex digit independently
      case-insensitive, *or* (for a space) a literal `+` (form
      encoding) -- so it matches arbitrary mixtures of raw and encoded
      characters, and arbitrary per-escape case mixing, in one pass. The
      regex is applied directly to the (possibly structurally-modified)
      text as plain pattern matching -- it never decodes/re-encodes a
      parsed URL or any surrounding prose -- so it catches a credential
      wherever it appears: a leaf, a URL/query/fragment component,
      embedded inside a longer backend error/result message, or a URL
      string itself embedded in a longer message, without any risk of
      corrupting an absolute URL or unrelated text around it. Secrets
      are caller-supplied credential material, not arbitrary/generic
      operator text, so there is no short-generic-value corruption risk
      to guard against.
    - `structural_redaction_values`: transient, non-secret
      operator-context identifiers (e.g. an already-existing Classic
      auth-server name, an AP-group target-group name, a device serial).
      These are redacted only structurally, via a whole-leaf comparison
      (handled inline below) plus `_redact_url_structural` for URL/query/
      fragment components: a whole leaf string that exactly equals one of
      them, or an exact decoded (percent-/form-encoded) path, query, or
      fragment component of a URL/endpoint string. They are never
      substring-replaced, because they can legitimately be as short
      as one character (see `_bounded_operator_string`) and a generic
      substring scan would corrupt unrelated prose that merely contains
      that character sequence (e.g. "ready", "wlan") anywhere else in the
      same payload.

    Both channels apply independently and can be combined in the same
    call; each keeps its own marker so the two redaction reasons stay
    distinguishable in the output. Per string, `structural_redaction_values`
    is always evaluated first, against the original, unmodified text, but
    the two structural cases behave differently:

    - Whole-leaf match: if `text` as a whole exactly equals one of
      `structural_redaction_values`, the structural marker is returned
      immediately, before any other pass runs. This is required, not
      cosmetic -- a legitimate operator identifier can embed a secret/
      placeholder literal as a mere substring (e.g.
      "prod-__runtime_secret_placeholder__-radius"); running the
      `secret_values` substring pass first would replace that inner
      slice, leaving the outer text unequal to the structural value it
      should have matched, so the whole-leaf comparison would silently
      fail and the identifier's prefix/suffix would leak unredacted.
      Short-circuiting on the original text before any other pass runs
      closes that gap, and is exact and total by construction: a
      whole-leaf match consumes the entire string, so there is nothing
      left for the secret pass to touch.
    - URL/query/fragment component match: exact decoded/percent-/form-
      encoded path, query, or fragment components of a URL/endpoint
      string are structurally redacted, producing a modified string --
      but this does NOT short-circuit. The same URL can have one
      component that is an exact structural operator-context match
      *and* another component (or an embedded fragment) that holds an
      actual runtime secret; both channels must run over the same string
      so neither leaks. The `secret_values` scan pass always runs
      afterward, over the (possibly structurally-modified) text, so a
      credential elsewhere in the URL/string -- raw, percent-encoded, or
      form-encoded -- is still caught.

    `_compiled_secrets` is private/internal (leading underscore, like
    `_depth`): callers never pass it. Each *top-level* call (`_depth == 0`,
    identified by `_compiled_secrets is None`) builds every distinct
    secret's linear `_SecretMatcher` exactly once via
    `_compile_secret_pattern`, then threads the already-built
    `dict[secret, matcher]` down through every recursive call in this call
    tree, so a leaf never rebuilds a matcher another leaf already built
    for the same secret. This is scoped to a single call/request -- built
    fresh on this stack, discarded when `_sanitize` returns, never stored
    on a module-level cache -- so no matcher (and no raw secret it was
    built from) outlives the call that needed it. `_SecretMatcher` is a
    plain Python object, never a compiled `re.Pattern`: no secret-derived
    regex, cache, or global object is ever produced anywhere in this
    module (see `_SecretMatcher`'s docstring for why).
    """
    secrets = tuple(secret for secret in secret_values if secret)
    structural_secrets = tuple(
        secret for secret in structural_redaction_values if secret
    )
    if _compiled_secrets is None:
        compiled_secrets = {
            secret: _compile_secret_pattern(secret) for secret in dict.fromkeys(secrets)
        }
    else:
        compiled_secrets = _compiled_secrets
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
                    structural_redaction_values=structural_secrets,
                    redact_marker=redact_marker,
                    structural_redact_marker=structural_redact_marker,
                    max_depth=max_depth,
                    _depth=_depth + 1,
                    _compiled_secrets=compiled_secrets,
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
                structural_redaction_values=structural_secrets,
                redact_marker=redact_marker,
                structural_redact_marker=structural_redact_marker,
                max_depth=max_depth,
                _depth=_depth + 1,
                _compiled_secrets=compiled_secrets,
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
        structural_secret_set = set(structural_secrets)
        # Whole-leaf structural match: the entire identifier is hidden
        # immediately, before any other pass runs, and the function
        # returns outright -- see the docstring above for why this must
        # happen first and must short-circuit. A whole-leaf match
        # consumes the entire string, so no secret-substring pass is
        # needed or run.
        if text in structural_secret_set:
            return structural_redact_marker
        # URL/query component structural redaction: modifies (but never
        # fully replaces) the string in place, and deliberately does NOT
        # return early -- unlike the whole-leaf case above, a URL can
        # have one component that is an exact structural operator-
        # context match *and* a different component, or an embedded
        # fragment, that holds an actual runtime secret. Both channels
        # must run over the same string so neither leaks; execution
        # falls through to the `secret_values` substring pass below on
        # whatever `_redact_url_structural` returns.
        text = _redact_url_structural(text, structural_secret_set, structural_redact_marker)
        # Actual secrets: one linear, regex-free `_SecretMatcher` per
        # secret (`_compile_secret_pattern`, built once per secret for
        # this top-level `_sanitize` call -- see `compiled_secrets`
        # above -- and reused here rather than rebuilt per leaf),
        # applied directly to the (possibly structurally-modified) text
        # via `_SecretMatcher.sub`, a plain-string scan/replace -- never
        # an `re.compile`d pattern. Each character of the secret is
        # matched as its raw literal form, its UTF-8 percent-encoded
        # byte sequence (every hex digit independently case-
        # insensitive), or -- for a space -- a literal `+` (form
        # encoding), so a single left-to-right scan catches any mixture
        # of raw/encoded characters and any per-escape case mixing,
        # whether the secret is a whole leaf, a URL/query/fragment
        # component, embedded inside a longer backend error/result
        # message, or inside a URL string that is itself embedded in a
        # longer message. This never decodes/re-encodes a parsed URL or
        # any surrounding prose -- it is a direct scan/replace over the
        # literal text -- so it cannot corrupt an absolute URL embedded
        # in prose, or any unrelated text around a match. The whole text
        # is scanned in full before any truncation -- see `_sanitize`'s
        # module-level note on `_SecretMatcher` -- so a secret can never
        # hide past a truncation boundary applied *before* this pass.
        for secret in secrets:
            text = compiled_secrets[secret].sub(redact_marker, text)
        if len(text) > 1000:
            return f"{text[:1000]}... [truncated {len(text) - 1000} chars]"
        return text
    return value


def _percent_encoded_char_literal(char: str) -> str:
    """Return the canonical (upper-case hex) UTF-8 percent-encoded byte
    sequence for `char` as a plain literal string -- one `%XX` per UTF-8
    byte, e.g. `%2F` for `/` or `%C3%A9` for `é`. This is never a regex
    fragment: `_secret_match_end` compares it against the corresponding
    slice of scanned text with `.upper()` applied to that slice, so any
    per-escape mixture of upper-/lower-case hex digits (`%2f`, `%2F`, or
    a mix within the same string) matches this one canonical literal
    without enumerating every case combination or building a character
    class.
    """
    return "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))


def _secret_char_pattern(char: str) -> tuple[str, ...]:
    """Return the literal alternative representations for one secret
    character: the raw literal character itself, its UTF-8 percent-
    encoded byte sequence (see `_percent_encoded_char_literal`), and --
    only for a space -- also a literal `+` (form encoding).

    These are plain strings, never a regex fragment or a compiled
    pattern -- `_compile_secret_pattern` walks a request-local tuple of
    these alternatives per character with direct string comparisons
    (`_secret_match_end`), so no secret-derived regex, `re.compile`
    call, or character class is ever produced anywhere in this module.

    The raw-literal and percent-encoded alternatives are mutually
    exclusive for any ordinary secret character -- a literal character
    can never begin with the `%` that starts the percent-encoded
    alternative -- but `_secret_match_end` still explores every
    alternative at each character position as a small, deduplicated
    frontier (never a first-alternative-wins guess), so a secret whose
    literal text itself contains `%` is still matched correctly without
    needing to backtrack into the ones not chosen first.
    """
    alternatives = (char, _percent_encoded_char_literal(char))
    if char == " ":
        return (*alternatives, "+")
    return alternatives


def _secret_match_end(
    text: str, start: int, alternatives: tuple[tuple[str, ...], ...]
) -> int | None:
    """Return the index in `text` just past a full match of `alternatives`
    (one tuple of per-character literal alternatives, in secret-character
    order -- see `_secret_char_pattern`) starting at `start`, or `None`
    if no full match starts there.

    Matching is a small non-deterministic-state-machine simulation --
    conceptually a Thompson-style NFA walk -- rather than a recursive,
    try-one-alternative-then-backtrack matcher: `frontier` holds every
    text position still consistent with the secret matched so far, and
    is rebuilt (deduplicated via a `set`) after each secret character is
    consumed. Because each character contributes at most 3 alternatives
    (see `_secret_char_pattern`), `frontier` never holds more than a
    handful of positions, so the whole walk costs `O(len(secret))` work
    per starting index -- linear in the secret length (bounded well
    below `aos8_target_adapters.MAX_SECRET_LENGTH` before a real secret
    ever reaches this function -- see
    `_validate_runtime_secret_lengths`) with a small constant factor,
    and no exponential/catastrophic-backtracking blowup regardless of
    secret content or text length: unlike naive backtracking, no failed
    path is ever retried, because every alternative at every position is
    already explored in the same forward pass.
    """
    n = len(text)
    frontier = {start}
    for char_alternatives in alternatives:
        if not frontier:
            return None
        next_frontier: set[int] = set()
        for pos in frontier:
            for alt in char_alternatives:
                end = pos + len(alt)
                if end > n:
                    continue
                if alt.startswith("%"):
                    if text[pos:end].upper() == alt:
                        next_frontier.add(end)
                elif text[pos:end] == alt:
                    next_frontier.add(end)
        frontier = next_frontier
    return min(frontier) if frontier else None


class _SecretMatcher:
    """A request-local, regex-free matcher for one secret's linear set of
    per-character alternatives (see `_secret_char_pattern`).

    Deliberately *not* backed by `re.compile`/`re.Pattern`: this plain
    Python object holds only a tuple of small per-character alternative
    tuples (built fresh by `_compile_secret_pattern` on every call, and
    discarded when the top-level `_sanitize` call that needed it
    returns) -- no compiled regex, module-level cache, `lru_cache`, or
    other global object anywhere in this module is ever built from, or
    retains, raw or percent-encoded secret material.

    `search`/`sub` mirror the small slice of the `re.Pattern` interface
    `_sanitize` and its regression tests need, but every match is found
    via `_secret_match_end`'s bounded state-machine walk, never regex
    alternation/backtracking.
    """

    __slots__ = ("_alternatives",)

    def __init__(self, alternatives: tuple[tuple[str, ...], ...]) -> None:
        self._alternatives = alternatives

    def search(self, text: str) -> tuple[int, int] | None:
        """Return `(start, end)` of the first full match of the secret
        anywhere in `text` (in any mixture of raw/percent-/form-encoded
        characters), or `None` if it does not occur at all."""
        if not self._alternatives:
            return None
        for start in range(len(text)):
            end = _secret_match_end(text, start, self._alternatives)
            if end is not None:
                return (start, end)
        return None

    def sub(self, replacement: str, text: str) -> str:
        """Replace every non-overlapping full match of the secret in
        `text` with `replacement`, scanning left to right and advancing
        past each match found. Applied directly to the literal
        characters already present in `text` -- this never decodes/
        re-encodes `text` itself, so it cannot corrupt an absolute URL
        embedded in prose or any unrelated text around a match."""
        if not self._alternatives:
            return text
        n = len(text)
        out: list[str] = []
        pos = 0
        while pos < n:
            end = _secret_match_end(text, pos, self._alternatives)
            if end is not None:
                out.append(replacement)
                pos = end
            else:
                out.append(text[pos])
                pos += 1
        return "".join(out)


def _compile_secret_pattern(secret: str) -> _SecretMatcher:
    """Build a request-local, regex-free `_SecretMatcher` for `secret`
    against arbitrary mixtures of raw and percent-/form-encoded
    characters -- e.g. the four characters `/?: ` can appear in
    backend-echoed text as any of `/?: `, `%2F%3f%3A%20`, `%2F%3f%3A+`,
    or any other per-character mixture of raw/encoded forms, with every
    `%XX` hex escape independently case-insensitive.

    Applied directly (via `_SecretMatcher.sub`) to the literal text -- it
    never decodes/re-encodes a parsed URL or any surrounding prose, so it
    cannot corrupt an absolute URL embedded in prose or any unrelated
    text around a match. Each secret character contributes a small,
    fixed-size set of alternatives (see `_secret_char_pattern`), walked
    by `_secret_match_end`'s bounded state-machine simulation, so
    matching is linear in `len(secret)` with no catastrophic-
    backtracking exposure regardless of secret length or content (also
    bounded well below `aos8_target_adapters.MAX_SECRET_LENGTH` before a
    real secret ever reaches this function -- see
    `_validate_runtime_secret_lengths`).

    Deliberately *not* cached across calls (no `lru_cache` or other
    module-level cache keyed by the secret, and never an `re.compile`d
    pattern in the first place): a cache keyed by raw secret text would
    retain every distinct secret -- and a secret-specific matcher built
    from it -- in process memory for the life of the process, long after
    the request/operation that supplied it has completed. `_sanitize`
    instead builds each secret's matcher once per top-level call via a
    local `compiled_secrets` dict threaded through its own recursion
    (see `_sanitize`'s docstring), so repeated construction is avoided
    within a single call tree without any secret surviving past it.
    """
    return _SecretMatcher(tuple(_secret_char_pattern(c) for c in secret))


def _is_url_like(text: str) -> bool:
    """Return True only for a standalone URL/endpoint leaf: an absolute
    URL beginning with an anchored, valid `scheme://` at the very start
    of `text`, or a path/query/fragment leaf beginning with `/`, `?`, or
    `#`. This is the narrow predicate `_redact_url_structural` uses to
    decide whether component-wise decoding is safe to attempt at all.

    Deliberately conservative: matching `"://"` anywhere in `text`
    (rather than an anchored scheme at the start) would let arbitrary
    prose that merely mentions or embeds a URL (e.g. "see
    https://example.com/x for detail") be parsed as if the *entire*
    string were that URL, corrupting everything before the scheme when
    path/query components are rewritten. This must never be broadened
    into a generic "does this look like it might contain a URL"
    heuristic -- see `_redact_url_components`.
    """
    return bool(_URL_SCHEME_RE.match(text)) or text.startswith(("/", "?", "#"))


def _split_url_parts(text: str) -> tuple[str, str, str, str, str]:
    """Split a URL/endpoint string into its path, query, and fragment
    parts, mirroring standard URL syntax (`path?query#fragment`) so both
    the structural and secret redaction passes decode/compare the same
    three logical components -- including the fragment, which earlier
    revisions of this module left folded into the tail of the query
    string (or the path, if there was no query), so a `#`-delimited
    fragment could never be matched as its own component.

    Returns `(path_part, query_sep, query_part, fragment_sep, fragment_part)`
    where the separators are `"?"`/`"#"` when present, or `""` when the
    corresponding part is absent -- an empty separator means "no query"/
    "no fragment", not "empty query"/"empty fragment", so callers can
    tell the two cases apart without a second partition.
    """
    body, fragment_sep, fragment_part = text.partition("#")
    path_part, query_sep, query_part = body.partition("?")
    return path_part, query_sep, query_part, fragment_sep, fragment_part


def _redact_url_structural(text: str, secret_set: set[str], redact_marker: str) -> str:
    """Redact `secret_set` from a URL/endpoint string `text` structurally
    -- never by arbitrary substring replacement, which would corrupt any
    unrelated text that merely *contains* a short/generic operator value
    as a fragment (e.g. an operator-supplied one-character value "a"
    would otherwise turn "ready" into "re<marker>dy" and "wlan" into
    "wl<marker>n" everywhere else in the same payload).

    The whole-leaf exact-match case is handled by the caller (`_sanitize`)
    before this function is ever reached; this function only handles the
    URL/query/fragment-component case: if `text` *is* a standalone URL/
    endpoint leaf (see `_is_url_like` -- an anchored `scheme://` at the
    very start, or a path/query/fragment leaf starting with `/`, `?`, or
    `#`), each `/`-delimited path segment, each `key`/`value` in a
    `?`-delimited query string, and each `key`/`value` in a
    `#`-delimited fragment is percent-/form-decoded and compared for an
    exact match, so a runtime value embedded as one path/query/fragment
    component is redacted without touching the rest of the endpoint
    string, whether it was passed decoded or percent-encoded. Non-URL
    text, a URL merely *embedded* inside longer prose (never a
    standalone leaf), and URL text with no matching component are all
    returned unchanged -- this function never short-circuits the
    caller's subsequent secret pass.

    Generic prose (adapter messages, statuses, warnings) that merely
    happens to share characters with a short operator value is left
    completely unchanged.
    """
    if not secret_set:
        return text
    if _is_url_like(text):
        return _redact_url_components(text, secret_set, redact_marker)
    return text


def _redact_url_components(text: str, secret_set: set[str], redact_marker: str) -> str:
    """Redact only exact decoded/percent-encoded path, query, or fragment
    components of a URL/path string `text`, never an arbitrary substring
    of it."""

    def redact_path_component(component: str) -> str:
        return redact_marker if unquote(component) in secret_set else component

    def redact_query_component(component: str) -> str:
        # Query (and fragment) components use form encoding, where `+`
        # represents a space -- `unquote_plus` decodes both that and
        # ordinary percent-escapes, so a value form-encoded with `+`
        # matches the same way a purely percent-encoded one does.
        return redact_marker if unquote_plus(component) in secret_set else component

    def redact_pair(pair: str) -> str:
        key, eq, val = pair.partition("=")
        if not eq:
            return redact_query_component(key)
        return f"{redact_query_component(key)}={redact_query_component(val)}"

    path_part, query_sep, query_part, fragment_sep, fragment_part = _split_url_parts(text)
    redacted_path = "/".join(redact_path_component(segment) for segment in path_part.split("/"))
    result = redacted_path
    if query_sep:
        redacted_query = "&".join(redact_pair(pair) for pair in query_part.split("&"))
        result = f"{result}{query_sep}{redacted_query}"
    if fragment_sep:
        redacted_fragment = "&".join(redact_pair(pair) for pair in fragment_part.split("&"))
        result = f"{result}{fragment_sep}{redacted_fragment}"
    return result


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


def _validate_runtime_secret_lengths(
    supplied_secrets: Mapping[str, Mapping[str, str]],
) -> None:
    """Reject any caller-supplied runtime secret (PSK, RADIUS/TACACS+
    shared secret, LDAP bind password, ...) longer than
    `MAX_SECRET_LENGTH` up front, before it reaches candidate mapping,
    any write invocation, or `_sanitize`'s per-character, regex-free
    `_SecretMatcher` construction (`_compile_secret_pattern`).

    This is `apply()`'s runtime counterpart to
    `aos8_target_adapters._secret_value`/`_secret_bundle_error`, which
    enforce the same bound once a `TargetContext` has been built for a
    specific candidate; this check runs first, over every supplied
    secret for the whole request, so an oversized value is refused
    outright rather than silently truncated, left to blow up a regex
    compilation, or discovered only candidate-by-candidate partway
    through a run.
    """
    oversized = sorted(
        f"{key}.{name}"
        for key, bundle in supplied_secrets.items()
        if isinstance(bundle, Mapping)
        for name, value in bundle.items()
        if isinstance(value, str) and len(value) > MAX_SECRET_LENGTH
    )
    if oversized:
        raise MigrationRunError(
            "Target secret inputs exceed the "
            f"{MAX_SECRET_LENGTH}-character runtime secret bound: {oversized}."
        )


def _bounded_operator_string(
    value: Any,
    field_name: str,
    *,
    max_length: int = MAX_OPERATOR_CONTEXT_STRING_LENGTH,
) -> str:
    """Structurally canonicalize and bound one free-form operator-context
    string: type, surrounding-whitespace trim, non-empty, and length only.
    Deliberately never a content/secret-word heuristic -- a legitimate
    Classic group or AP group literally named "Token-Group" or
    "private-key-infra" must be accepted unchanged (see the module note
    above `_validate_external_object_references`).
    """
    if not isinstance(value, str):
        raise MigrationRunError(f"{field_name} must be a string.")
    text = value.strip()
    if not text:
        raise MigrationRunError(f"{field_name} must be a non-empty string.")
    if len(text) > max_length:
        raise MigrationRunError(f"{field_name} exceeds {max_length} characters.")
    return text


# `external_object_references`, `ap_group_target_map`, and
# `ap_group_device_serials` carry operator-declared *reference* strings (an
# existing object's name, an AP-group/Classic-group name, a device serial).
# Their names and values are arbitrary caller-chosen identifiers -- e.g. a
# Classic auth-server profile literally named "Token-Group", or an AP group
# named "private-key-infra" -- so they must never be screened with
# secret-keyword/secret-shaped-content heuristics (`_is_sensitive_key`):
# those heuristics are only sound against dictionary field names with a
# known, fixed schema (e.g. a candidate payload's "shared_secret" field),
# not against free-form operator identifiers. Structural bounds (type,
# non-empty, whitespace-trimmed, length, count) are the only validation
# applied here. The actual secret-persistence risk is eliminated by
# accepting these three maps only from the stateless `preview()` path and
# rejecting any non-empty map outright everywhere else (see
# `_reject_persisted_operator_context` below) -- never by storing the
# values, a hash, a count, or any other resupply metadata.


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
# `ap_group_device_serials` are accepted only by the stateless `preview()`
# path. Every persistent workflow rejects any non-empty map outright (see
# `_reject_persisted_operator_context`) instead of storing the values, a
# hash, a count, or any other resupply metadata -- there is no verifier
# for these free-form operator identifiers, so persisting even a hash
# would create an offline-guessing surface. `_without_operator_context`
# simply drops the (guaranteed-empty, by the time it is called) keys
# before a target dict is persisted, so a persisted `target` never even
# carries the keys -- matching the 0.4 shape these fields did not exist in.
_OPERATOR_CONTEXT_FIELDS = (
    "external_object_references",
    "ap_group_target_map",
    "ap_group_device_serials",
)


def _reject_persisted_operator_context(
    target: Mapping[str, Any], *, workflow: str
) -> None:
    """Fail closed, with a clear and actionable error, if a persistent
    workflow (`create_run`, and by construction anything that only ever
    operates on an already-persisted run's stored target) is asked to use
    a non-empty operator-context map. These maps may be used transiently
    to construct a stateless `preview()` response only; call
    `aos8_preview_migration_run` for that instead.
    """
    offending = [field for field in _OPERATOR_CONTEXT_FIELDS if target.get(field)]
    if offending:
        raise MigrationRunError(
            f"{workflow} cannot accept a non-empty "
            f"{', '.join(sorted(offending))}: operator-context maps are "
            "accepted only by aos8_preview_migration_run's stateless "
            "preview, which does not persist a migration run. Remove "
            "them from this call, or use aos8_preview_migration_run to "
            "review the same mapping without creating a run."
        )


def _without_operator_context(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in target.items() if key not in _OPERATOR_CONTEXT_FIELDS
    }


# Stable, distinct marker for a transiently-supplied operator-context value
# (e.g. a WPA3-Enterprise auth-server reference, an AP-group target-group
# name, or a device serial) that leaked into a stateless preview's
# operations/payloads/blockers/warnings. Deliberately different from the
# generic secret marker ("******") used for key-based redaction elsewhere
# in `_sanitize`, so the two redaction reasons stay distinguishable.
_RUNTIME_CONTEXT_REDACTED_MARKER = "<runtime-context-redacted>"
# Generic, count-free/value-free markers used in place of the raw
# `external_object_references`/`ap_group_target_map`/
# `ap_group_device_serials` maps in a stateless preview's echoed
# `target` -- they must show *that* a runtime mapping was (or was not)
# supplied for structural/status purposes, never the map's keys, values,
# or size.
_RUNTIME_CONTEXT_SUPPLIED = "runtime mapping supplied"
_RUNTIME_CONTEXT_NOT_SUPPLIED = "runtime mapping not supplied"


def _operator_context_marker(mapping: Mapping[str, Any]) -> str:
    return _RUNTIME_CONTEXT_SUPPLIED if mapping else _RUNTIME_CONTEXT_NOT_SUPPLIED


def _operator_context_redaction_values(
    external_object_references: Mapping[str, Mapping[str, str]],
    ap_group_target_map: Mapping[str, str],
    ap_group_device_serials: Mapping[str, Iterable[str]],
) -> tuple[str, ...]:
    """Collect every operator-*supplied* leaf string from the three
    transient operator-context maps, to be used as exact-match redaction
    input by `_sanitize(..., structural_redaction_values=...)`.

    Deliberately excludes the maps' own keys: `ap_group_target_map`'s and
    `ap_group_device_serials`' keys are AOS8 AP-group names that already
    equal that candidate's own `identifier` (already shown elsewhere in
    the same preview), and `external_object_references`'s top-level keys
    are candidate keys (`object_type:identifier`), likewise already
    public within the same response. Only the *values* -- an
    already-existing Classic auth-server name, a Classic target-group
    name, and device serial numbers -- are genuinely runtime-supplied,
    potentially-identifying data that must never be echoed back.
    """
    values: list[str] = []
    for refs in external_object_references.values():
        for ref_value in refs.values():
            if isinstance(ref_value, str) and ref_value:
                values.append(ref_value)
    for target_group in ap_group_target_map.values():
        if isinstance(target_group, str) and target_group:
            values.append(target_group)
    for serials in ap_group_device_serials.values():
        for serial in serials:
            if isinstance(serial, str) and serial:
                values.append(serial)
    return tuple(values)


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


_LEGACY_OPERATOR_CONTEXT_MARKER = "legacy_operator_context_sanitized"
_LEGACY_CANDIDATE_BLOCKED_MESSAGE = (
    "This candidate's prior result predates this run's operator-context "
    "sanitization and cannot be trusted; recreate the run with "
    "aos8_create_migration_run."
)
# Run-level activity timestamps that record *when* a dry-run/apply/verify
# was last attempted against this run. Each one is set from a candidate
# write/verify pass that may have used the now-removed operator-context
# state to build its payload, so -- like each candidate's own
# `attempts`/`attempt_history`/`last_result`/`verification` -- they are
# untrusted execution history and must be reset to `None`, not merely left
# in place, when healing a stale run. `checkpoint_and_rollback` is
# deliberately excluded: it is static, adapter-type-only guidance (see
# `BaseCentralTargetAdapter.checkpoint_guidance`) that never varies with
# operator-context values or candidate data, so it carries no
# pre-sanitization context to reset.
_LEGACY_RUN_ACTIVITY_FIELDS = (
    "dry_run_attempted_at",
    "last_apply_at",
    "last_verification_at",
)


def _heal_legacy_candidate_entry(entry: Any) -> Any:
    """Reset every field on one candidate entry that could carry a result,
    attempt count, error, or verification record computed while this run
    still held (or was built from) unsafe operator-context state.

    Unlike the raw `target` operator-context values, these cannot be
    exact-match-redacted: the original operator-context values that may
    have shaped a prior write's payload/result are already gone by the
    time this runs, so there is nothing left to match against. They are
    cleared outright -- including the numeric `attempts` counter, which
    is untrusted execution history in its own right (it reflects retries
    made against a payload built from the now-removed operator-context
    values) -- and the candidate is marked durably blocked with a
    generic, value-free message, rather than left holding possibly-tainted
    data. Only the creation identity fields needed to identify the
    candidate (`key`, `candidate`, `requires_secret_input`,
    `required_secret_names`) survive unchanged.
    """
    if not isinstance(entry, Mapping):
        return entry
    healed = dict(entry)
    healed["status"] = "blocked"
    healed["retryable"] = False
    healed["attempts"] = 0
    healed["dry_run_ok"] = False
    healed["last_error"] = _LEGACY_CANDIDATE_BLOCKED_MESSAGE
    healed["last_result"] = None
    healed["attempt_history"] = []
    healed["verification"] = None
    return healed


def _sanitize_legacy_operator_context(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Heal a genuinely stale state file written before this fail-closed
    contract existed: one that still carries raw
    `external_object_references`/`ap_group_target_map`/
    `ap_group_device_serials` values directly on `target`, and/or the
    non-reversible `operator_context_metadata` fingerprint/count metadata
    an earlier revision persisted instead of the raw values.

    Removing those two fields is not enough on its own: any candidate
    result/error/attempt/attempt-history/verification record recorded
    while the run held that unsafe state, the run-level
    `dry_run_attempted_at`/`last_apply_at`/`last_verification_at`
    activity timestamps from those same attempts, and the run's own
    `fingerprint` (itself derived from `target`+candidates and therefore
    potentially an operator-derived hash), could still encode
    operator-supplied values or counts. All of that is removed/reset
    here too -- never just the two triggering fields -- and the run and
    every candidate are marked durably blocked/recreate-required with a
    generic message. Only creation identity/source metadata needed to
    identify the run (`run_id`, `schema_version`, `created_at`, the
    sanitized `target`) survives untouched. Never mutates `value` in
    place; returns `(possibly-sanitized run, whether anything changed)`.
    """
    changed = False
    sanitized = dict(value)
    target = sanitized.get("target")
    if isinstance(target, dict) and any(
        field in target for field in _OPERATOR_CONTEXT_FIELDS
    ):
        sanitized["target"] = _without_operator_context(target)
        changed = True
    if "operator_context_metadata" in sanitized:
        del sanitized["operator_context_metadata"]
        changed = True
    if changed:
        healed_target = (
            sanitized["target"] if isinstance(sanitized.get("target"), dict) else {}
        )
        healed_candidates = [
            _heal_legacy_candidate_entry(entry)
            for entry in sanitized.get("candidates", [])
        ]
        sanitized["candidates"] = healed_candidates
        sanitized["status"] = "blocked"
        sanitized["updated_at"] = _now()
        # Every run-level activity timestamp reflects an attempt made
        # against the now-removed operator-context state; reset each one
        # to `None` (the same "never attempted" value `create_run` uses),
        # exactly like each candidate's own reset attempt/history fields
        # above -- these are untrusted execution history, not identity.
        for field in _LEGACY_RUN_ACTIVITY_FIELDS:
            sanitized[field] = None
        # The stored `fingerprint` (and the removed
        # `operator_context_metadata`'s embedded hash, if present) may
        # have been derived, directly or indirectly, from the now-removed
        # raw operator-context values -- recompute it purely from the
        # sanitized target/candidates so no operator-derived hash
        # survives on disk; there is no raw value left to reuse, so this
        # is a fresh, independent fingerprint, not the old one.
        sanitized["fingerprint"] = _run_fingerprint(
            [
                entry.get("candidate", {})
                for entry in healed_candidates
                if isinstance(entry, Mapping)
            ],
            healed_target,
            None,
        )
        sanitized[_LEGACY_OPERATOR_CONTEXT_MARKER] = {
            "removed_at": _now(),
            "reason": (
                "This run's on-disk state was written by a prior revision "
                "that could persist operator-supplied "
                "external_object_references/ap_group_target_map/"
                "ap_group_device_serials values, or a resupply fingerprint "
                "derived from them. Those fields, this run's fingerprint, "
                "every candidate's prior attempts/attempt-history/"
                "result/error/verification record, and this run's "
                "dry_run_attempted_at/last_apply_at/last_verification_at "
                "activity timestamps have all been removed or reset -- "
                "none of them can be trusted to be free of "
                "operator-derived values or counts. This run is durably "
                "blocked and cannot be applied: recreate it with "
                "aos8_create_migration_run, and use a fresh "
                "aos8_preview_migration_run first for any "
                "context-dependent mapping (e.g. WPA3-Enterprise, "
                "AP-group)."
            ),
        }
    return sanitized, changed


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
        validated = validate_run_id(run_id)
        with self.lock_run(validated):
            return self._load_locked(validated)

    def _load_locked(self, run_id: str) -> dict[str, Any]:
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
        # A state file written before this fail-closed contract existed
        # (or hand-edited) could still carry raw operator-context values on
        # `target`, or the non-reversible fingerprint/count metadata an
        # earlier revision persisted instead. Heal it now: rewrite the
        # actual file with those fields removed and a durable warning
        # marker added, so a stale on-disk value is never served back
        # through `get_run`/`list_runs`/`apply`/`verify`, and the healed
        # version -- not just an in-memory copy -- is what is on disk
        # afterward.
        sanitized, changed = _sanitize_legacy_operator_context(value)
        if changed:
            self._write_locked(sanitized)
        return sanitized

    def save(self, run: Mapping[str, Any]) -> None:
        run_id = validate_run_id(str(run.get("run_id", "")))
        # Hard backstop, not a silent sanitizer: normal code paths
        # (`create_run`) must reject a non-empty operator-context map
        # before ever calling `save()` -- see
        # `_reject_persisted_operator_context`. If one somehow reaches
        # here regardless, that is a bug in the caller and `save()` fails
        # loudly rather than quietly persisting or dropping it.
        target = run.get("target")
        if isinstance(target, dict):
            offending = [
                field for field in _OPERATOR_CONTEXT_FIELDS if target.get(field)
            ]
            if offending:
                raise MigrationRunError(
                    f"Refusing to persist run {run_id!r}: {', '.join(offending)} "
                    "is non-empty. Operator-context maps must be rejected "
                    "before a persistent workflow ever calls save()."
                )
            if any(field in target for field in _OPERATOR_CONTEXT_FIELDS):
                run = {**run, "target": _without_operator_context(target)}
        with self.lock_run(run_id):
            self._write_locked(run)

    def _write_locked(self, run: Mapping[str, Any]) -> None:
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
        # Present only when `MigrationRunStore.load()` healed a genuinely
        # stale state file that predated this fail-closed contract (see
        # `_sanitize_legacy_operator_context`). A durable warning, never a
        # value or hash: this run cannot be applied and must be recreated.
        "legacy_operator_context_sanitized": run.get(
            _LEGACY_OPERATOR_CONTEXT_MARKER
        ),
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
        # -- but that does not mean the raw operator-context values (an
        # already-existing Classic auth-server name, an AP-group target
        # group name, device serials) are safe to echo back: they are
        # still runtime-supplied, potentially-identifying data. This flag
        # documents only that they are never *persisted*, not that they
        # are shown unredacted below.
        preview["operator_context_persisted"] = False
        context = adapter.context
        # Replace the raw `target.external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` echo with a
        # generic, value-free/count-free marker: the caller can see
        # *whether* a runtime mapping was supplied for this preview, never
        # its keys, values, or size.
        preview["target"] = {
            **preview.get("target", {}),
            "external_object_references": _operator_context_marker(
                context.external_object_references
            ),
            "ap_group_target_map": _operator_context_marker(
                context.ap_group_target_map
            ),
            "ap_group_device_serials": _operator_context_marker(
                context.ap_group_device_serials
            ),
        }
        # Defense in depth: the same raw operator-context values may still
        # have been used transiently to construct operation payloads
        # elsewhere in this preview (e.g. WPA3-Enterprise's `auth_server1`
        # in a create/update payload). Scrub every exact occurrence
        # recursively -- operations, payloads, blockers, warnings, and
        # results alike -- and replace it with a stable, distinct marker
        # rather than leaving the operator-supplied value (or a
        # derived/identifying count) in the returned preview. These are
        # non-secret operator identifiers (can legitimately be as short as
        # one character), so they go through the exact-match-only
        # `structural_redaction_values` channel, never a substring scan.
        redaction_values = _operator_context_redaction_values(
            context.external_object_references,
            context.ap_group_target_map,
            context.ap_group_device_serials,
        )
        # `preview()` also transiently injects a fixed, non-secret
        # `__runtime_secret_placeholder__` literal (via `placeholders=True`
        # / `_placeholder_secret_inputs`) into any operation payload field
        # that requires real target secrets, so callers can see the shape
        # of a WPA3-Enterprise/auth-server write without ever holding real
        # credentials. It is not itself sensitive and there is no real
        # leak path that requires redacting it, so it is deliberately kept
        # out of the aggressive-substring `secret_values` channel: routing
        # it there would let it collide with -- and corrupt -- a
        # legitimate operator-context identifier that merely happens to
        # embed the placeholder literal as a substring (e.g.
        # "prod-__runtime_secret_placeholder__-radius"), stripping only
        # the inner slice and leaking the surrounding prefix/suffix before
        # the exact-match structural comparison against `redaction_values`
        # ever runs. The placeholder is left to appear verbatim in the
        # preview; only genuine operator-supplied identifiers go through
        # `structural_redaction_values` below.
        return _sanitize(
            preview,
            structural_redaction_values=redaction_values,
            structural_redact_marker=_RUNTIME_CONTEXT_REDACTED_MARKER,
        )

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
        # Fail closed before doing anything else: `external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` are accepted only
        # by the stateless `preview()` path. A persistent run must never be
        # created from a target that carries a non-empty one.
        _reject_persisted_operator_context(
            target, workflow="aos8_create_migration_run"
        )
        safe_candidates = self._validate_candidates(candidates)
        selected_set = set(selected) if selected is not None else None
        adapter = self._adapter(target, safe_candidates, placeholders=True)
        full_preview = adapter.preview(safe_candidates, selected=selected_set)
        persisted_target = _without_operator_context(full_preview["target"])
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
                    "last_error": _sanitize(joined_errors) if joined_errors else None,
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
        # Fail closed before touching the run at all: an oversized secret
        # is a caller-input error independent of any specific run's
        # state, so it is rejected up front -- before mapping, any write
        # invocation, or `_sanitize` -- rather than partway through
        # per-candidate processing below.
        _validate_runtime_secret_lengths(target_secrets or {})
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
        # A run healed from a genuinely stale, pre-fix state file (see
        # `_sanitize_legacy_operator_context`) may have been created with
        # operator context that could change how its candidates map --
        # that context is gone now (never stored, never resuppliable), so
        # this run must not be applied at all: it must be recreated.
        if run.get(_LEGACY_OPERATOR_CONTEXT_MARKER):
            raise MigrationRunError(
                f"Migration run {run_id!r} contained unsafe legacy "
                "operator-context data that has been removed from its "
                "on-disk state (see run['legacy_operator_context_sanitized'])."
                " It cannot be applied: recreate it with "
                "aos8_create_migration_run."
            )
        effective_target = run["target"]
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
        # Persistent runs never carry `external_object_references`/
        # `ap_group_target_map`/`ap_group_device_serials` (rejected at
        # `create_run()` time -- see `_reject_persisted_operator_context`),
        # so no operator context is available or needed here regardless:
        # WPA3-Enterprise is unconditionally `dry_run_only` (real execution
        # always refused) and AP-group mappings never leave `unsupported`
        # (contract matrix §5/§6.11), so neither family can ever reach the
        # terminal-success state `verify()` inspects.
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
