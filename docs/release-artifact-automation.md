# Release artifact automation (v0.7)

This page documents the credential-gated validation matrix, the release
bundle packaging pipeline, and the restore/smoke-test tooling that produce
and verify the artifacts a v0.7 release publishes. It defers global tool
counts, the package version, README aggregate metrics, release notes, and
full GitHub Pages reconciliation to the release-integration pass -- see
[Artifact contracts and live-test configuration](artifact-contracts.md) for
the underlying schemas and the credential-gated live-test config these
tools all reuse.

None of the tooling on this page makes a live vendor API call or writes to
a real platform by default. Every read/write live probe stays gated behind
`pipeline.live_test_config` (`CENTRALMCP_LIVE_TEST_<PLATFORM>_READ=1` /
`_WRITE=1`), and this page's scripts never flip those flags themselves.

## Validation matrix

```bash
uv run python scripts/run_v07_validation_matrix.py
```

Classifies every v0.7 coverage category -- Central, GLP, AOS8, the optional
product starters (ClearPass, Mist, Apstra, EdgeConnect, UXI), Axis, RAG/
source freshness, and router automation -- into exactly one of six states,
without ever making a live call itself:

| Classification | Meaning |
|---|---|
| `offline_fixture` | Only the offline evaluator self-check ran; no credentials or opt-in. |
| `live_read` | Read opt-in (`_READ=1`) and credentials are both present. |
| `disposable_write` | Read **and** write opt-in are both set (write alone is never sufficient) and credentials are present. |
| `blocked` | Safe default when no offline self-check exists and live-read opt-in is not enabled. |
| `unavailable` | Read opt-in is enabled but credentials are missing, or a required offline helper failed. |
| `coverage_gap` | A documented permanent or currently unverified capability gap, such as a missing live write API or unavailable planner surface. |

The runner delegates every product's actual classification logic to that
product's existing evaluator/report script (`scripts/evaluate_central_070_readonly.py`,
`scripts/evaluate_axis_lab.py`, `scripts/build_optional_product_evidence.py`,
`scripts/generate_router_automation_report.py`, and friends) instead of
duplicating any of it. The result is a `VALIDATION_MATRIX_RESULT` artifact
(`pipeline/artifact_contracts.py`), written via `contracts.write_artifact`
like every other v0.7 artifact kind.

```bash
uv run python scripts/run_v07_validation_matrix.py --output outputs/validation-matrix.json
```

## Release bundle packaging

```bash
uv run python scripts/build_release_bundle.py --output-dir dist
```

Assembles one release-artifacts bundle end to end:

1. Validation matrix (`evidence/validation-matrix.json`).
2. Capability snapshot (`evidence/capability-snapshot.json`, the
   reproducible core of `scripts/report_capability_gaps.py`).
3. Source-freshness snapshot, only if a prior local
   `outputs/source-freshness.json` already exists -- never fetched here.
4. Optional-product-backend compatibility/evidence artifacts.
5. Axis lab evidence, router dependency/reconciliation plan artifacts.
6. Prebuilt RAG/OpenAPI indexes under `indexes/`, only if `data/` already
   contains them locally (skip with `--no-indexes`).
7. `release-manifest.json` (a `RELEASE_ARTIFACT_MANIFEST` artifact) listing
   every staged file's kind, schema version, size, SHA-256, and redaction
   status.
8. `sbom.json` -- a deterministic CycloneDX 1.5 SBOM generated from
   `uv.lock` by `pipeline/sbom.py` (component name/version/purl only; no
   network resolution).
9. `CHECKSUMS.txt` -- a `sha256sum`-compatible checksums file covering
   every staged file (never lists itself).
10. `provenance.json` -- a provenance manifest (`pipeline/release_packaging.py`
    `build_provenance_manifest`) recording the release version, builder
    identity (`local` or `github-actions`), and the SHA-256 subject list.
    It is explicitly **not** a signed attestation; GitHub artifact
    attestation happens separately, in CI, over the final archive.
11. A deterministic `.tar.gz` archive (sorted member order; fixed
    `mtime=0`/`uid=0`/`gid=0`/`mode=0o644` tar metadata; fixed gzip header)
    plus its own `.sha256` sidecar.

"Deterministic" describes the archive **packaging mechanics**, not the
staged content byte-for-byte across time: evidence files legitimately embed
a fresh `generated_at` timestamp on every run, exactly like every other
artifact kind. Given byte-identical staged input, `build_deterministic_archive`
always produces a byte-identical archive.

`pipeline/release_packaging.py` intentionally never imports anything from
`scripts/` (the repository's `pipeline/` → `scripts/` layering rule), so all
of the multi-step orchestration above -- which does need several sibling
`scripts/*` evidence generators -- lives in `scripts/build_release_bundle.py`
instead.

## Restore and smoke-test

```bash
uv run python scripts/restore_release_bundle.py dist/centralmcp-release-artifacts-v<version>.tar.gz
```

Generalizes `scripts/download_indexes.py`'s safe-extraction pattern
(`pipeline/release_restore.py`) and adds:

- File-count / per-file / total-size bounds, enforced **before** any bytes
  are written (defaults: 1000 members, 1 GiB per file, 2 GiB total -- sized
  for this repo's prebuilt RAG indexes, which can be several hundred MB).
- Rejection of path traversal, absolute paths, and any non-regular-file /
  non-directory archive member (symlinks, hardlinks, devices).
- A hard refusal to extract into the repository root or any guarded
  top-level source directory (`pipeline`, `scripts`, `tests`, `docs`,
  `mcp_servers`, `config`, `ingestion`, `resources`, `inputs`, `.git`) --
  restore/smoke-testing a bundle never overwrites repository data.
- Checksum verification against a sibling `.sha256` file when present.
- Post-extraction schema validation: every file the bundle's own
  `release-manifest.json` lists is located, its size/SHA-256 are re-checked
  against the manifest record, and its JSON payload is re-validated against
  `pipeline.artifact_contracts.build_artifact` for that entry's `kind`.
  `sbom.json`/`provenance.json` get a lighter structural sanity check.
- Extraction only into a caller-managed temporary directory
  (`tempfile.TemporaryDirectory`), always cleaned up -- even on failure.

## GitHub Actions

`.github/workflows/release-artifacts.yml` runs on a `v*` tag push or manual
`workflow_dispatch`: build the bundle → restore/smoke-test it →
`actions/upload-artifact` → (on tag pushes only)
`actions/attest-build-provenance`, using least-required permissions
(`contents: read` by default; `id-token: write` and `attestations: write`
only on the one job that needs them). It never publishes a GitHub Release
itself and never runs on a schedule -- the existing scheduled
source-freshness job stays in `ci.yml`, unchanged.

`actions/attest-build-provenance`'s `subject-checksums` input resolves file
paths relative to the runner's working directory (the repo root), while
this repo's own `.sha256` sidecar convention (matching
`scripts/package_indexes.py`) stores only the archive's basename. The
workflow's "Prepare attestation subject checksums" step re-formats the
*same already-computed* digest with the repo-root-relative archive path --
it never recomputes a hash.

## Testing and linting

```bash
uv run pytest \
  tests/unit/test_artifact_contracts.py \
  tests/unit/test_run_v07_validation_matrix.py \
  tests/unit/test_sbom.py \
  tests/unit/test_release_packaging.py \
  tests/unit/test_build_release_bundle.py \
  tests/unit/test_release_restore.py \
  tests/unit/test_release_artifacts_workflow.py

uv run ruff check pipeline/sbom.py pipeline/release_packaging.py pipeline/release_restore.py \
  scripts/run_v07_validation_matrix.py scripts/build_release_bundle.py scripts/restore_release_bundle.py
```

These tests never make a network call and never enable a live-test flag;
`tests/unit/test_release_artifacts_workflow.py` parses the workflow YAML
offline and asserts the least-privilege/pinning/gating properties above
without invoking GitHub Actions.
