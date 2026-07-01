from __future__ import annotations

import os
from pathlib import Path

from scripts import ingest_tools, validate_release

README = validate_release.ROOT / "README.md"
RAG_ARCHITECTURE = validate_release.ROOT / "docs" / "architecture" / "RAG-ARCHITECTURE.md"
MIN_TOOLS = validate_release._DEFAULT_MIN_TOOLS


def test_rag_indexes_available_false_when_missing(tmp_path: Path):
    assert validate_release._rag_indexes_available(tmp_path) is False


def test_rag_indexes_available_true_when_present(tmp_path: Path):
    (tmp_path / "data/docs.lance").mkdir(parents=True)
    (tmp_path / "data/specs.sqlite").write_text("")

    assert validate_release._rag_indexes_available(tmp_path) is True


def test_tool_catalog_count_includes_optional_products():
    assert validate_release._tool_catalog_count("all") >= MIN_TOOLS


def test_optional_product_catalog_can_filter_writes_for_read_only(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    read_only_names = {
        tool["name"]
        for server, tool in ingest_tools._collect("clearpass")
        if server == "clearpass-core"
    }

    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-write")
    read_write_names = {
        tool["name"]
        for server, tool in ingest_tools._collect("clearpass")
        if server == "clearpass-core"
    }

    assert "clearpass_status" in read_only_names
    assert "clearpass_write" not in read_only_names
    assert "clearpass_write" in read_write_names


def test_release_tool_catalog_count_uses_read_write_catalog(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-only")
    read_only_count = len(ingest_tools._collect("clearpass"))

    assert validate_release._tool_catalog_count("clearpass") > read_only_count
    assert os.environ["CENTRALMCP_PRODUCT_ACCESS"] == "read-only"


def test_public_docs_tool_counts_match_catalog():
    core_count = validate_release._tool_catalog_count(None)
    previous_access = os.environ.get("CENTRALMCP_PRODUCT_ACCESS")
    os.environ["CENTRALMCP_PRODUCT_ACCESS"] = "read-only"
    try:
        read_only_count = len(ingest_tools._collect("all"))
    finally:
        if previous_access is None:
            os.environ.pop("CENTRALMCP_PRODUCT_ACCESS", None)
        else:
            os.environ["CENTRALMCP_PRODUCT_ACCESS"] = previous_access
    read_write_count = validate_release._tool_catalog_count("all")
    expected = (
        f"{core_count} core tools / {read_only_count} read-only optional starters / "
        f"{read_write_count} read-write optional starters"
    )

    assert expected in README.read_text()
    assert expected in RAG_ARCHITECTURE.read_text()


def test_validate_tool_count_accepts_count_at_floor():
    validate_release._validate_tool_count(MIN_TOOLS, MIN_TOOLS)


def test_validate_tool_count_rejects_count_below_floor():
    try:
        validate_release._validate_tool_count(MIN_TOOLS - 1, MIN_TOOLS)
    except SystemExit as exc:
        assert "below required minimum" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_validate_tool_index_fresh_accepts_equal_count():
    validate_release._validate_tool_index_fresh(MIN_TOOLS, MIN_TOOLS)


def test_validate_tool_index_fresh_rejects_stale_index():
    try:
        validate_release._validate_tool_index_fresh(MIN_TOOLS - 1, MIN_TOOLS)
    except SystemExit as exc:
        assert "Tool index is stale" in str(exc)
        assert "scripts/ingest_tools.py --products all" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
