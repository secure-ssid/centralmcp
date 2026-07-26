from __future__ import annotations

import argparse
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


def test_registered_tool_identities_len_matches_tool_catalog_count():
    identities = validate_release._registered_tool_identities("clearpass")

    assert len(identities) == validate_release._tool_catalog_count("clearpass")
    assert all(":" in identity for identity in identities)


def test_indexed_tool_identities_none_when_table_missing(tmp_path: Path):
    assert validate_release._indexed_tool_identities(tmp_path) is None


def test_indexed_tool_identities_reflect_tools_table_rows(tmp_path: Path):
    from pipeline.clients import lance_client

    (tmp_path / "data").mkdir()
    db = lance_client.connect(tmp_path / "data")
    lance_client.create_tools_table(
        db,
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "server": "aruba-config",
                "name": "list_ssids",
                "description": "List SSIDs",
                "schema_json": "{}",
                "fts_text": "list ssids",
                "vector": [0.0, 0.0],
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "server": "aruba-ops",
                "name": "reboot_device",
                "description": "Reboot a device",
                "schema_json": "{}",
                "fts_text": "reboot device",
                "vector": [0.0, 0.0],
            },
        ],
    )

    identities = validate_release._indexed_tool_identities(tmp_path)

    assert identities == {"aruba-config:list_ssids", "aruba-ops:reboot_device"}


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


def test_release_all_catalog_includes_generated_glp_and_restores_env(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_GLP_GENERATED_TOOLS", "0")

    release_count = validate_release._tool_catalog_count("all")

    assert release_count > len(ingest_tools._collect("all"))
    assert os.environ["CENTRALMCP_GLP_GENERATED_TOOLS"] == "0"


def test_public_docs_tool_counts_match_catalog():
    core_count = len(ingest_tools._collect())
    previous_access = os.environ.get("CENTRALMCP_PRODUCT_ACCESS")
    os.environ["CENTRALMCP_PRODUCT_ACCESS"] = "read-only"
    try:
        read_only_count = len(ingest_tools._collect("all"))
        os.environ["CENTRALMCP_PRODUCT_ACCESS"] = "read-write"
        read_write_count = len(ingest_tools._collect("all"))
    finally:
        if previous_access is None:
            os.environ.pop("CENTRALMCP_PRODUCT_ACCESS", None)
        else:
            os.environ["CENTRALMCP_PRODUCT_ACCESS"] = previous_access
    expected = (
        f"{core_count} core tools / {read_only_count} read-only optional starters / "
        f"{read_write_count} read-write optional starters"
    )

    assert expected in README.read_text()
    assert expected in RAG_ARCHITECTURE.read_text()


def test_positive_int_accepts_positive_values():
    assert validate_release._positive_int("1") == 1
    assert validate_release._positive_int(str(MIN_TOOLS)) == MIN_TOOLS


def test_positive_int_rejects_non_positive_and_non_integer_values():
    for raw in ("0", "-1", "1.5", "abc"):
        try:
            validate_release._positive_int(raw)
        except argparse.ArgumentTypeError as exc:
            assert "positive integer" in str(exc)
        else:
            raise AssertionError(f"expected ArgumentTypeError for {raw!r}")


def test_cli_defaults_min_tools_to_release_floor():
    args = validate_release._build_parser().parse_args([])

    assert args.min_tools == MIN_TOOLS


def test_cli_accepts_positive_min_tools():
    args = validate_release._build_parser().parse_args(["--min-tools", "1"])

    assert args.min_tools == 1


def test_cli_rejects_non_positive_min_tools(capsys):
    for raw in ("0", "-5"):
        try:
            validate_release._build_parser().parse_args(["--min-tools", raw])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"expected SystemExit for --min-tools {raw}")
        assert "positive integer" in capsys.readouterr().err


def test_validate_tool_count_accepts_count_at_floor():
    validate_release._validate_tool_count(MIN_TOOLS, MIN_TOOLS)


def test_validate_tool_count_rejects_count_below_floor():
    try:
        validate_release._validate_tool_count(MIN_TOOLS - 1, MIN_TOOLS)
    except SystemExit as exc:
        assert "below required minimum" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def _identity_set(n: int) -> set[str]:
    return {f"server:tool_{i}" for i in range(n)}


def test_validate_tool_index_fresh_accepts_identical_identity_sets():
    identities = _identity_set(MIN_TOOLS)
    validate_release._validate_tool_index_fresh(identities, identities)


def test_validate_tool_index_fresh_rejects_missing_tools():
    registered = _identity_set(MIN_TOOLS)
    indexed = _identity_set(MIN_TOOLS - 1)
    try:
        validate_release._validate_tool_index_fresh(indexed, registered)
    except SystemExit as exc:
        assert "Tool index is stale" in str(exc)
        assert "missing from the index" in str(exc)
        assert "scripts/ingest_tools.py --products all" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_validate_tool_index_fresh_rejects_same_count_but_different_identities():
    # Same total count on both sides -- a count-only comparison would have
    # called this "fresh" -- but one tool was swapped for a different one.
    registered = _identity_set(MIN_TOOLS)
    indexed = _identity_set(MIN_TOOLS) - {"server:tool_0"} | {"server:renamed_tool"}

    try:
        validate_release._validate_tool_index_fresh(indexed, registered)
    except SystemExit as exc:
        message = str(exc)
        assert "Tool index is stale" in message
        assert "missing from the index" in message
        assert "no longer registered" in message
        assert "server:tool_0" in message
        assert "server:renamed_tool" in message
    else:
        raise AssertionError("expected SystemExit")


def test_bounded_preview_truncates_long_identity_lists():
    identities = sorted(_identity_set(50))

    preview = validate_release._bounded_preview(identities)

    assert preview.endswith(", ...")
    assert preview.count(",") == validate_release._BOUNDED_PREVIEW_LIMIT
