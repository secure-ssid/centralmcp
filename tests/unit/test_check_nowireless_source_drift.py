"""Tests for scripts.check_nowireless_source_drift.

Covers: baseline-vs-latest path-commit comparison (never the whole-tree pin
compared directly against a path commit), current pins, drift, multiple
watched paths, dedup of identical (repo, ref, path) records, request/URL
construction (``sha`` present only on the baseline query), the
Authorization header being sent but never leaked into output,
malformed/empty GitHub responses, network errors on either the baseline or
latest fetch, pinned-metadata inconsistency, and main() exit-code/summary
behavior (distinguishing fetch/config errors from confirmed drift). No live
network calls -- urllib.request.urlopen and the module-level fetch helper
are always monkeypatched.
"""

from __future__ import annotations

import json

import pytest

from scripts import check_nowireless_source_drift as drift

PINNED_REF = "da9c834651f2a6a3842544b3aac7d3a48da7f766"


# ---------------------------------------------------------------------------
# normalize_repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "nowireless4u/hpe-networking-mcp",
        "https://github.com/nowireless4u/hpe-networking-mcp",
        "https://github.com/nowireless4u/hpe-networking-mcp/",
        "https://github.com/nowireless4u/hpe-networking-mcp.git",
        "github.com/nowireless4u/hpe-networking-mcp",
    ],
)
def test_normalize_repo_accepts_url_and_owner_repo_forms(value):
    assert drift.normalize_repo(value) == "nowireless4u/hpe-networking-mcp"


def test_normalize_repo_rejects_malformed_identifier():
    with pytest.raises(drift.DriftConfigError):
        drift.normalize_repo("not a repo identifier")


# ---------------------------------------------------------------------------
# Pin loaders re-derive from existing source-of-truth modules
# ---------------------------------------------------------------------------


def test_load_glp_pin_matches_generator_constants():
    pin = drift.load_glp_pin()
    assert pin["repo"] == "nowireless4u/hpe-networking-mcp"
    assert pin["path"] == "vendor/greenlake"
    assert pin["ref"]


def test_load_axis_pin_matches_generator_constants():
    pin = drift.load_axis_pin()
    assert pin["repo"] == "nowireless4u/hpe-networking-mcp"
    assert pin["path"] == "src/hpe_networking_mcp/platforms/axis"
    assert pin["ref"]


def _write_benchmark(tmp_path, **overrides):
    data = {
        "repository": "nowireless4u/hpe-networking-mcp",
        "repository_url": "https://github.com/nowireless4u/hpe-networking-mcp",
        "commit": PINNED_REF,
        "source_evidence": [
            {"claim": "a", "path": "README.md"},
            {"claim": "b", "path": "README.md"},
        ],
        "indexed_endpoint_reproduction": {"source_script": "scripts/build_spec_index.py"},
    }
    data.update(overrides)
    path = tmp_path / "capability-benchmark-snapshot.json"
    path.write_text(json.dumps(data))
    return path


def test_load_capability_benchmark_pins_dedupes_readme_source_evidence(tmp_path):
    path = _write_benchmark(tmp_path)

    pins = drift.load_capability_benchmark_pins(path)

    paths = sorted(pin["path"] for pin in pins)
    assert paths == ["README.md", "scripts/build_spec_index.py"]


def test_load_capability_benchmark_pins_rejects_repo_url_mismatch(tmp_path):
    path = _write_benchmark(
        tmp_path, repository_url="https://github.com/someoneelse/hpe-networking-mcp"
    )

    with pytest.raises(drift.DriftConfigError, match="mismatch"):
        drift.load_capability_benchmark_pins(path)


def test_load_capability_benchmark_pins_rejects_missing_commit(tmp_path):
    path = _write_benchmark(tmp_path, commit="")

    with pytest.raises(drift.DriftConfigError):
        drift.load_capability_benchmark_pins(path)


def test_load_capability_benchmark_pins_rejects_no_watched_paths(tmp_path):
    path = _write_benchmark(tmp_path, source_evidence=[], indexed_endpoint_reproduction={})

    with pytest.raises(drift.DriftConfigError, match="no watched"):
        drift.load_capability_benchmark_pins(path)


def test_load_capability_benchmark_pins_rejects_malformed_json(tmp_path):
    path = tmp_path / "capability-benchmark-snapshot.json"
    path.write_text("not json")

    with pytest.raises(drift.DriftConfigError, match="not valid JSON"):
        drift.load_capability_benchmark_pins(path)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_dedupe_inputs_merges_identical_repo_ref_path_records():
    entries = [
        {"label": "a", "repo": "r/r", "ref": "sha1", "path": "p"},
        {"label": "b", "repo": "r/r", "ref": "sha1", "path": "p"},
        {"label": "c", "repo": "r/r", "ref": "sha1", "path": "other"},
    ]

    merged = drift.dedupe_inputs(entries)

    assert len(merged) == 2
    first = next(m for m in merged if m["path"] == "p")
    assert first["labels"] == ["a", "b"]


# ---------------------------------------------------------------------------
# collect_watched_inputs: consistency + dedup end-to-end
# ---------------------------------------------------------------------------


def test_collect_watched_inputs_dedupes_and_includes_all_watched_paths(tmp_path):
    path = _write_benchmark(tmp_path)

    entries = drift.collect_watched_inputs(benchmark_path=path)

    watched_paths = {e["path"] for e in entries}
    assert watched_paths == {
        "vendor/greenlake",
        "src/hpe_networking_mcp/platforms/axis",
        "README.md",
        "scripts/build_spec_index.py",
    }
    readme_entry = next(e for e in entries if e["path"] == "README.md")
    assert readme_entry["labels"] == ["capability_benchmark:README.md"]


def test_collect_watched_inputs_rejects_inconsistent_repository(tmp_path, monkeypatch):
    path = _write_benchmark(
        tmp_path,
        repository="someoneelse/hpe-networking-mcp",
        repository_url="https://github.com/someoneelse/hpe-networking-mcp",
    )

    with pytest.raises(drift.DriftConfigError, match="inconsistent pinned repository"):
        drift.collect_watched_inputs(benchmark_path=path)


def test_collect_watched_inputs_raises_when_benchmark_file_missing(tmp_path):
    with pytest.raises(drift.DriftConfigError):
        drift.collect_watched_inputs(benchmark_path=tmp_path / "missing.json")


# ---------------------------------------------------------------------------
# fetch_path_commit -- request construction, malformed data, network errors
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._payload


def test_fetch_path_commit_baseline_query_includes_sha_and_path(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse(json.dumps([{"sha": "baseline123"}]).encode())

    monkeypatch.setattr(drift.urllib.request, "urlopen", fake_urlopen)

    sha = drift.fetch_path_commit(
        "nowireless4u/hpe-networking-mcp",
        "vendor/greenlake",
        sha=PINNED_REF,
    )

    assert sha == "baseline123"
    assert f"sha={PINNED_REF}" in captured["url"]
    assert "path=vendor" in captured["url"]
    assert "per_page=1" in captured["url"]


def test_fetch_path_commit_latest_query_omits_sha(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse(json.dumps([{"sha": "latest456"}]).encode())

    monkeypatch.setattr(drift.urllib.request, "urlopen", fake_urlopen)

    sha = drift.fetch_path_commit(
        "nowireless4u/hpe-networking-mcp",
        "vendor/greenlake",
    )

    assert sha == "latest456"
    assert "sha=" not in captured["url"]
    assert "path=vendor" in captured["url"]
    assert "per_page=1" in captured["url"]


def test_fetch_path_commit_sends_bearer_token_header_without_leaking(monkeypatch):
    captured = {}
    fake_token = "test-fixture-token-value"

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.headers)
        return _FakeResponse(json.dumps([{"sha": "abc123"}]).encode())

    monkeypatch.setattr(drift.urllib.request, "urlopen", fake_urlopen)

    drift.fetch_path_commit(
        "nowireless4u/hpe-networking-mcp",
        "vendor/greenlake",
        token=fake_token,
    )

    header_value = captured["headers"]["Authorization"]
    assert header_value.startswith("Bearer")
    assert fake_token in header_value


def test_fetch_path_commit_omits_authorization_header_without_token(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.headers)
        return _FakeResponse(json.dumps([{"sha": "abc123"}]).encode())

    monkeypatch.setattr(drift.urllib.request, "urlopen", fake_urlopen)

    drift.fetch_path_commit("nowireless4u/hpe-networking-mcp", "vendor/greenlake", token=None)

    assert "Authorization" not in captured["headers"]


def test_fetch_path_commit_raises_on_empty_commit_list(monkeypatch):
    monkeypatch.setattr(
        drift.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps([]).encode()),
    )

    with pytest.raises(drift.DriftFetchError, match="no commits returned"):
        drift.fetch_path_commit("r/r", "p")


def test_fetch_path_commit_raises_on_malformed_non_list_payload(monkeypatch):
    monkeypatch.setattr(
        drift.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps({"message": "bad"}).encode()),
    )

    with pytest.raises(drift.DriftFetchError, match="no commits returned"):
        drift.fetch_path_commit("r/r", "p")


def test_fetch_path_commit_raises_on_missing_sha_field(monkeypatch):
    monkeypatch.setattr(
        drift.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps([{"commit": {}}]).encode()),
    )

    with pytest.raises(drift.DriftFetchError, match="missing sha"):
        drift.fetch_path_commit("r/r", "p")


def test_fetch_path_commit_raises_on_non_json_payload(monkeypatch):
    monkeypatch.setattr(
        drift.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(b"not json"),
    )

    with pytest.raises(drift.DriftFetchError, match="malformed"):
        drift.fetch_path_commit("r/r", "p")


def test_fetch_path_commit_raises_on_http_error(monkeypatch):
    import urllib.error

    def _raise(request, timeout=None):
        raise urllib.error.HTTPError("url", 404, "Not Found", None, None)

    monkeypatch.setattr(drift.urllib.request, "urlopen", _raise)

    with pytest.raises(drift.DriftFetchError, match="HTTP 404"):
        drift.fetch_path_commit("r/r", "p")


def test_fetch_path_commit_raises_on_url_error(monkeypatch):
    import urllib.error

    def _raise(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(drift.urllib.request, "urlopen", _raise)

    with pytest.raises(drift.DriftFetchError, match="network error"):
        drift.fetch_path_commit("r/r", "p")


# ---------------------------------------------------------------------------
# evaluate_input -- baseline-vs-latest comparison, never tree-pin-vs-path
# ---------------------------------------------------------------------------


def _entry(path="p", ref="tree-pin-sha", labels=None):
    return {"repo": "r/r", "ref": ref, "path": path, "labels": labels or ["label"]}


def _patch_path_commit(monkeypatch, *, baseline, latest):
    """Route baseline (sha= kwarg present) and latest (no sha) calls separately."""

    def fake(repo, path, *, sha=None, token=None, timeout=None):
        if sha is not None:
            if isinstance(baseline, Exception):
                raise baseline
            return baseline
        if isinstance(latest, Exception):
            raise latest
        return latest

    monkeypatch.setattr(drift, "fetch_path_commit", fake)


def test_evaluate_input_current_when_baseline_equals_latest_even_if_tree_pin_differs(
    monkeypatch,
):
    """Regression: a whole-tree pin that is nothing like the path commit SHA
    must still report CURRENT as long as the path hasn't changed since the
    pin (baseline == latest). The tree pin itself is never compared to a
    path commit."""
    _patch_path_commit(monkeypatch, baseline="path-commit-abc", latest="path-commit-abc")

    result = drift.evaluate_input(_entry(ref="totally-unrelated-tree-pin-sha"))

    assert result["status"] == drift.STATUS_CURRENT
    assert result["baseline"] == "path-commit-abc"
    assert result["latest"] == "path-commit-abc"
    assert result["pin"] == "totally-unrelated-tree-pin-sha"


def test_evaluate_input_drift_only_when_latest_differs_from_baseline(monkeypatch):
    _patch_path_commit(monkeypatch, baseline="path-commit-old", latest="path-commit-new")

    result = drift.evaluate_input(_entry(ref="totally-unrelated-tree-pin-sha"))

    assert result["status"] == drift.STATUS_DRIFT
    assert result["baseline"] == "path-commit-old"
    assert result["latest"] == "path-commit-new"


def test_evaluate_input_calls_fetch_path_commit_with_pin_as_baseline_sha(monkeypatch):
    calls = []

    def fake(repo, path, *, sha=None, token=None, timeout=None):
        calls.append({"sha": sha})
        return "same-sha"

    monkeypatch.setattr(drift, "fetch_path_commit", fake)

    drift.evaluate_input(_entry(ref="the-pin"))

    assert calls[0]["sha"] == "the-pin"
    assert calls[1]["sha"] is None


def test_evaluate_input_error_on_baseline_fetch_failure(monkeypatch):
    _patch_path_commit(
        monkeypatch, baseline=drift.DriftFetchError("baseline boom"), latest="unused"
    )

    result = drift.evaluate_input(_entry())

    assert result["status"] == drift.STATUS_ERROR
    assert "baseline" in result["detail"]
    assert "boom" in result["detail"]
    assert result["baseline"] is None
    assert result["latest"] is None


def test_evaluate_input_error_on_latest_fetch_failure(monkeypatch):
    _patch_path_commit(
        monkeypatch, baseline="baseline-sha", latest=drift.DriftFetchError("latest boom")
    )

    result = drift.evaluate_input(_entry())

    assert result["status"] == drift.STATUS_ERROR
    assert "latest" in result["detail"]
    assert "boom" in result["detail"]
    # Baseline succeeded before latest failed -- bounded, not None.
    assert result["baseline"] == "baseline-sha"
    assert result["latest"] is None


# ---------------------------------------------------------------------------
# format_result_line -- compact, bounded rendering
# ---------------------------------------------------------------------------


def test_format_result_line_shows_pin_baseline_and_latest_compactly():
    result = {
        "repo": "r/r",
        "path": "p",
        "pin": "a" * 40,
        "baseline": "b" * 40,
        "latest": "c" * 40,
        "labels": ["x"],
        "status": drift.STATUS_CURRENT,
        "detail": "no path changes since reviewed pin",
    }

    line = drift.format_result_line(result)

    assert "CURRENT" in line
    assert "pin=" + "a" * 12 in line
    assert "baseline=" + "b" * 12 in line
    assert "latest=" + "c" * 12 in line
    assert "a" * 40 not in line  # bounded, full SHA never rendered


def test_format_result_line_truncates_excess_labels():
    result = {
        "repo": "r/r",
        "path": "p",
        "pin": "sha1",
        "baseline": "sha2",
        "latest": "sha2",
        "labels": [f"label{i}" for i in range(10)],
        "status": drift.STATUS_CURRENT,
        "detail": "no path changes since reviewed pin",
    }

    line = drift.format_result_line(result)

    assert "label0" in line
    assert "..." in line
    assert "label9" not in line


# ---------------------------------------------------------------------------
# main(): exit codes and summary across multiple watched paths
# ---------------------------------------------------------------------------


def _patch_collect(monkeypatch, entries):
    monkeypatch.setattr(drift, "collect_watched_inputs", lambda **kw: entries)


def test_main_exits_zero_when_all_current(monkeypatch, capsys):
    entries = [
        {"repo": "r/r", "ref": "tree-pin", "path": "a", "labels": ["a"]},
        {"repo": "r/r", "ref": "tree-pin", "path": "b", "labels": ["b"]},
    ]
    _patch_collect(monkeypatch, entries)
    _patch_path_commit(monkeypatch, baseline="same-sha", latest="same-sha")

    exit_code = drift.main([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "2 current, 0 drifted, 0 fetch errors." in out


def test_main_exits_nonzero_on_any_drifted_path_among_multiple(monkeypatch, capsys):
    entries = [
        {"repo": "r/r", "ref": "tree-pin", "path": "a", "labels": ["a"]},
        {"repo": "r/r", "ref": "tree-pin", "path": "b", "labels": ["b"]},
    ]
    _patch_collect(monkeypatch, entries)

    def fake(repo, path, *, sha=None, token=None, timeout=None):
        if sha is not None:
            return "baseline-sha"
        return "baseline-sha" if path == "a" else "changed-sha"

    monkeypatch.setattr(drift, "fetch_path_commit", fake)

    exit_code = drift.main([])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "1 current, 1 drifted, 0 fetch errors." in out
    assert "Drift detected" in out


def test_main_exits_nonzero_on_fetch_error_and_labels_it_distinctly_from_drift(monkeypatch, capsys):
    _patch_collect(monkeypatch, [{"repo": "r/r", "ref": "tree-pin", "path": "a", "labels": ["a"]}])
    _patch_path_commit(
        monkeypatch, baseline=drift.DriftFetchError("connection refused"), latest="unused"
    )

    exit_code = drift.main([])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "0 current, 0 drifted, 1 fetch errors." in out
    assert "could not be checked" in out
    assert "Drift detected" not in out


def test_main_exits_nonzero_on_config_error(monkeypatch, capsys):
    def _raise(**kw):
        raise drift.DriftConfigError("inconsistent pinned repository across modules")

    monkeypatch.setattr(drift, "collect_watched_inputs", _raise)

    exit_code = drift.main([])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Configuration error" in err


def test_main_exits_nonzero_when_no_watched_inputs(monkeypatch):
    _patch_collect(monkeypatch, [])

    assert drift.main([]) == 1


def test_main_does_not_leak_token_into_stdout_or_stderr(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "leaked-token-should-not-persist")
    _patch_collect(monkeypatch, [{"repo": "r/r", "ref": "tree-pin", "path": "a", "labels": ["a"]}])

    captured_token = {}

    def fake(repo, path, *, sha=None, token=None, timeout=None):
        captured_token["token"] = token
        return "same-sha"

    monkeypatch.setattr(drift, "fetch_path_commit", fake)

    exit_code = drift.main([])

    assert exit_code == 0
    assert captured_token["token"] == "leaked-token-should-not-persist"
    captured = capsys.readouterr()
    assert "leaked-token-should-not-persist" not in captured.out
    assert "leaked-token-should-not-persist" not in captured.err
