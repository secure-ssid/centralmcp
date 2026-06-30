from __future__ import annotations

from pathlib import Path

from scripts import validate_release


def test_rag_indexes_available_false_when_missing(tmp_path: Path):
    assert validate_release._rag_indexes_available(tmp_path) is False


def test_rag_indexes_available_true_when_present(tmp_path: Path):
    (tmp_path / "data/docs.lance").mkdir(parents=True)
    (tmp_path / "data/specs.sqlite").write_text("")

    assert validate_release._rag_indexes_available(tmp_path) is True


def test_tool_catalog_count_includes_optional_products():
    assert validate_release._tool_catalog_count("all") >= 204


def test_validate_tool_count_accepts_count_at_floor():
    validate_release._validate_tool_count(204, 204)


def test_validate_tool_count_rejects_count_below_floor():
    try:
        validate_release._validate_tool_count(203, 204)
    except SystemExit as exc:
        assert "below required minimum" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_validate_tool_index_fresh_accepts_equal_count():
    validate_release._validate_tool_index_fresh(204, 204)


def test_validate_tool_index_fresh_rejects_stale_index():
    try:
        validate_release._validate_tool_index_fresh(203, 204)
    except SystemExit as exc:
        assert "Tool index is stale" in str(exc)
        assert "scripts/ingest_tools.py --products all" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
