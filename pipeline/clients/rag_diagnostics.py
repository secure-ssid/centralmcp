"""Bounded ingestion-delta and source-freshness diagnostics for RAG.

Two read-only diagnostics, both scoped to the security-advisory/lifecycle
source families this v0.7 workstream owns (not the full prose corpus, which
is other workstreams' ingestion concern), and both reusing existing building
blocks instead of reimplementing them:

1. :func:`ingestion_delta` — new/changed/removed/unchanged content-hash
   counts, computed against the current LanceDB ``docs`` table. Reuses
   ``ingestion.ingest_docs.collect_points``/``content_hash`` — the exact
   functions ``ingest_docs.py --incremental`` uses — purely to *diff*; it
   never embeds a vector or writes a row (no live writes).
2. :func:`freshness_summary` — reduces the ``source_freshness_result``
   artifact written by ``scripts/check_security_lifecycle_drift.py`` to
   per-status counts plus each source's bounded entry, re-validated through
   ``pipeline.artifact_contracts`` so a malformed/stale file is rejected
   loudly instead of silently misread.

Neither function makes a network call, and neither exposes a raw source
body — only counts, statuses, and the already-bounded ``detail`` strings
the persisted artifact/table already carry.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

from pipeline import artifact_contracts as contracts

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "ingestion" / "sources"
DEFAULT_FRESHNESS_ARTIFACT = ROOT / "outputs" / "source-freshness.json"

# The source families this diagnostic is scoped to. Kept in sync with
# pipeline/clients/advisory_index.SOURCE_DIRS, which indexes the same
# folders into the structured advisories/lifecycle_events tables.
DELTA_SOURCE_FAMILIES: tuple[str, ...] = (
    "security_advisories",
    "juniper_security_advisories",
    "lifecycle_notices",
    "juniper_lifecycle",
)

_DOC_TYPE_BY_FAMILY: dict[str, str] = {
    "security_advisories": "security-advisory",
    "juniper_security_advisories": "security-advisory",
    "lifecycle_notices": "lifecycle",
    "juniper_lifecycle": "lifecycle",
}


def ingestion_delta(
    sources: tuple[str, ...] | None = None,
    *,
    sources_dir: Path = SOURCES_DIR,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Return new/changed/removed/unchanged content-hash counts, per source.

    Read-only: computes current chunk content hashes with the same
    ``ingestion.ingest_docs.collect_points``/``content_hash`` helpers the
    incremental ingester uses, and diffs them against the existing LanceDB
    ``docs`` table metadata. Never embeds a vector or writes a row.

    Args:
        sources: Source-family folder names to diff (default: all four
            security/lifecycle families this diagnostic is scoped to).
        sources_dir: Override for ``ingestion/sources`` (tests only). Note
            ``ingest_docs.collect_points`` computes each record's
            ``file_path`` relative to its own module-level
            ``ingest_docs.SOURCES_DIR`` constant, so a test overriding this
            must also monkeypatch that constant to the same root.
        data_dir: Override for the LanceDB ``data/`` directory (tests only).

    Returns:
        ``{"sources": {family: {status, new, changed, removed, unchanged}}}``.
        ``status`` is ``not_yet_indexed`` when the family has zero existing
        rows in the docs table (nothing to diff against yet, not an error),
        or ``missing_source_dir`` when the source folder does not exist.
    """
    from ingestion import ingest_docs
    from pipeline.clients import lance_client

    families = tuple(sources) if sources else DELTA_SOURCE_FAMILIES
    unknown = [family for family in families if family not in DELTA_SOURCE_FAMILIES]
    if unknown:
        raise ValueError(
            f"unsupported source families for this diagnostic: {unknown}; "
            f"choose from {DELTA_SOURCE_FAMILIES}"
        )

    connect_kwargs = {} if data_dir is None else {"data_dir": data_dir}
    db = lance_client.connect(**connect_kwargs)

    existing_by_source: dict[str, dict[str, str]] = {}
    table_exists = lance_client.docs_table(db) is not None
    has_content_hash = "content_hash" in lance_client.docs_columns(db)
    legacy_table = table_exists and not has_content_hash
    if has_content_hash:
        for row in lance_client.docs_metadata(db):
            existing_by_source.setdefault(row.get("source", ""), {})[str(row["id"])] = str(
                row.get("content_hash") or ""
            )

    results: dict[str, Any] = {}
    for family in families:
        source_dir = sources_dir / family
        if not source_dir.exists():
            results[family] = {
                "status": "missing_source_dir",
                "new": 0,
                "changed": 0,
                "removed": 0,
                "unchanged": 0,
            }
            continue

        # collect_points prints progress lines; suppress them so this stays
        # safe to call from a stdio-transport MCP tool (stdout carries the
        # JSON-RPC stream there).
        with contextlib.redirect_stdout(io.StringIO()):
            records = ingest_docs.collect_points(source_dir, _DOC_TYPE_BY_FAMILY[family])

        current = {str(record["id"]): str(record["content_hash"]) for record in records}
        if legacy_table:
            results[family] = {
                "status": "full_rebuild_required",
                "new": len(current),
                "changed": 0,
                "removed": 0,
                "unchanged": 0,
            }
            continue
        existing = existing_by_source.get(family, {})
        new_count = sum(1 for doc_id in current if doc_id not in existing)
        changed_count = sum(
            1
            for doc_id, digest in current.items()
            if doc_id in existing and existing[doc_id] != digest
        )
        removed_count = sum(1 for doc_id in existing if doc_id not in current)
        unchanged_count = len(current) - new_count - changed_count
        results[family] = {
            "status": "indexed" if existing else "not_yet_indexed",
            "new": new_count,
            "changed": changed_count,
            "removed": removed_count,
            "unchanged": unchanged_count,
        }

    return {"sources": results}


def freshness_summary(
    artifact_path: Path = DEFAULT_FRESHNESS_ARTIFACT,
) -> dict[str, Any]:
    """Reduce the persisted ``source_freshness_result`` artifact to status counts.

    Re-validates the artifact through ``pipeline.artifact_contracts`` so a
    malformed or schema-mismatched file is rejected loudly instead of
    silently misread. Returns per-status counts plus each source's bounded
    entry (``source``, ``count``, ``minimum``, ``status``, ``detail``) —
    never a raw source body.

    Raises:
        FileNotFoundError: no artifact has been generated yet — run
            ``scripts/check_security_lifecycle_drift.py`` to create one.
        contracts.ArtifactValidationError: the file exists but fails schema
            validation (stale/incompatible shape).
    """
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"No source-freshness artifact at {artifact_path}; run "
            "scripts/check_security_lifecycle_drift.py to generate one"
        )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    snapshot = contracts.build_artifact(contracts.SOURCE_FRESHNESS_RESULT, payload)
    data = contracts.to_json_dict(snapshot)

    status_counts: dict[str, int] = {}
    for entry in data["entries"]:
        status_counts[entry["status"]] = status_counts.get(entry["status"], 0) + 1

    return {
        "generated_at": data["generated_at"],
        "schema_version": data["schema_version"],
        "status_counts": status_counts,
        "entries": data["entries"],
    }
