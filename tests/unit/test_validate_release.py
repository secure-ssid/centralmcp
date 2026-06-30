from __future__ import annotations

from pathlib import Path

from scripts import validate_release

README = validate_release.ROOT / "README.md"
RAG_ARCHITECTURE = validate_release.ROOT / "docs" / "architecture" / "RAG-ARCHITECTURE.md"


def test_rag_indexes_available_false_when_missing(tmp_path: Path):
    assert validate_release._rag_indexes_available(tmp_path) is False


def test_rag_indexes_available_true_when_present(tmp_path: Path):
    (tmp_path / "data/docs.lance").mkdir(parents=True)
    (tmp_path / "data/specs.sqlite").write_text("")

    assert validate_release._rag_indexes_available(tmp_path) is True


def test_tool_catalog_count_includes_optional_products():
    assert validate_release._tool_catalog_count("all") >= 204


def test_public_docs_tool_counts_match_catalog():
    core_count = validate_release._tool_catalog_count(None)
    optional_count = validate_release._tool_catalog_count("all")
    expected = f"{core_count} core tools, or {optional_count} with optional product starters"
    compact_expected = f"{core_count} core tools / {optional_count} with optional product starters"

    assert expected in README.read_text()
    assert compact_expected in RAG_ARCHITECTURE.read_text()


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
