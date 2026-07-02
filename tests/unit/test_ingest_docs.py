"""Regression tests for ingestion/ingest_docs.py.

Covers three defects found in a review pass:
- _schema_to_text crashed on a non-dict property schema (a valid JSON Schema
  boolean schema, e.g. `"properties": {"x": true}`), aborting the entire
  ingestion run instead of skipping that one field.
- collect_points hashed stable_id from a cwd-relative Path, so the same
  logical file got a different id depending on how the script was invoked
  (breaks redis-backend incremental dedup on rerun).
"""

from __future__ import annotations

import os
from pathlib import Path

from ingestion.ingest_docs import _schema_to_text, _md5_uuid, collect_points, stable_id


def test_schema_to_text_skips_boolean_property_schema():
    schema = {
        "properties": {
            "weirdField": True,
            "normalField": {"type": "string", "description": "a normal field"},
        }
    }

    text = _schema_to_text("spec", "Schema1", schema)

    assert text is not None
    assert "normalField" in text
    assert "weirdField" not in text


def test_schema_to_text_skips_null_property_schema():
    schema = {"properties": {"weirdField": None, "normalField": {"type": "string"}}}

    text = _schema_to_text("spec", "Schema1", schema)

    assert "normalField" in text
    assert "weirdField" not in text


def test_schema_to_text_returns_none_when_only_non_dict_properties():
    schema = {"properties": {"weirdField": True}}

    assert _schema_to_text("spec", "Schema1", schema) is None


def test_collect_points_ids_are_invariant_to_path_taken_to_reach_the_file(tmp_path):
    """Reaching the same logical file via a symlinked path (simulating a
    different invocation style/cwd, e.g. `uv run python ingestion/ingest_docs.py`
    from repo root vs `python -m ingestion.ingest_docs`) must produce the same
    chunk id — otherwise redis-backend incremental dedup treats every chunk
    as new on a rerun and duplicates the whole corpus."""
    real_root = tmp_path / "real_sources"
    real_folder = real_root / "nac_docs"
    real_folder.mkdir(parents=True)
    (real_folder / "a.md").write_text("# Title\n\nSome body text.\n")

    link_root = tmp_path / "link_sources"
    os.symlink(real_root, link_root)
    link_folder = link_root / "nac_docs"

    import ingestion.ingest_docs as m

    original_sources_dir = m.SOURCES_DIR
    try:
        m.SOURCES_DIR = real_root
        records_real = collect_points(real_folder, "nac")

        m.SOURCES_DIR = link_root
        records_link = collect_points(link_folder, "nac")
    finally:
        m.SOURCES_DIR = original_sources_dir

    assert [r["id"] for r in records_real] == [r["id"] for r in records_link]
    assert records_real[0]["file_path"] == records_link[0]["file_path"] == "nac_docs/a.md"


def test_stable_id_is_path_object_hash_of_given_path():
    assert stable_id(Path("nac_docs/a.md"), 0) == _md5_uuid("nac_docs/a.md:0")
